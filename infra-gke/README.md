# GKE infrastructure for the production OCR pipeline

This directory provisions the Google Cloud infrastructure required by the repository's
existing [`k8s/gke`](../k8s/gke) manifests. Terraform owns cloud infrastructure; the
Kubernetes manifests remain the source of truth for workloads.

## What Terraform creates

- Required Google Cloud APIs (left enabled on destroy).
- A dedicated VPC-native network and subnet with Pod and Service secondary ranges.
- A regional Artifact Registry Docker repository.
- A least-privilege GKE node service account.
- One **zonal GKE Standard** cluster with Workload Identity and Filestore CSI enabled.
- Five node pools:

| Pool | Purpose | Machine/GPU | Autoscaling | Taint |
|---|---|---|---|---|
| `system` | GKE system pods, KEDA, Prometheus, Grafana | `e2-standard-4` | 1-3 | none |
| `redisnp` | Redis state and queue | `n2-highmem-4` | 1-3 | `sku=redis:NoSchedule` |
| `apinp` | Rust ingestion API | `n2-standard-2` | 1-5 | `sku=api:NoSchedule` |
| `gpunpt4` | PP-DocLayoutV3 worker | `n1-standard-4` + 1 T4 16 GB | **0-4** | `nvidia.com/gpu=present:NoSchedule` |
| `gpunpa100` | Qwen 3.5 4B with vLLM | `a2-ultragpu-1g` + 1 A100 80 GB | **0-4** | `nvidia.com/gpu=present:NoSchedule` |

GKE installs and manages the NVIDIA drivers and device plugin. Do **not** install the
NVIDIA GPU Operator used by AKS.

The cluster is zonal by design. A regional GKE cluster can create `--num-nodes` in each
zone and requires scarce GPU capacity in multiple zones. Pinning the cluster to a zone
known to offer both GPU types keeps the node count and quota behavior predictable.

## 1. Install the local tools

### macOS (Homebrew)

```bash
brew update
brew install --cask gcloud-cli docker
brew install terraform kubectl helm
```

The `gcloud-cli` cask installs the Google Cloud CLI distribution, including `gcloud`,
`gsutil`, and `bq`. Start Docker Desktop once after installation. If `kubectl` later
reports that the GKE auth plugin is missing, install it with:

```bash
gcloud components install gke-gcloud-auth-plugin
export USE_GKE_GCLOUD_AUTH_PLUGIN=True
```

### Linux

Install the Google Cloud CLI using Google's package instructions:
<https://cloud.google.com/sdk/docs/install-sdk>. Then install Terraform, `kubectl`,
Helm, and Docker from their official package repositories:

- <https://developer.hashicorp.com/terraform/install>
- <https://kubernetes.io/docs/tasks/tools/install-kubectl-linux/>
- <https://helm.sh/docs/intro/install/>
- <https://docs.docker.com/engine/install/>

Install the GKE auth plugin if it was not included:

```bash
gcloud components install gke-gcloud-auth-plugin
```

Verify all tools before continuing:

```bash
gcloud version
terraform version
kubectl version --client
helm version
docker version
```

## 2. Create and activate a Google Cloud account

1. Create an account at <https://cloud.google.com/free>.
2. Create a project in the Cloud Console and record its immutable **project ID**.
3. Link a billing account and **activate/upgrade the account out of Free Trial**. Trial
   projects cannot receive GPU quota. Remaining trial credit is retained after upgrade.
4. Ensure your identity can enable project services and create VPC, IAM, Artifact
   Registry, GKE, and Filestore resources. Project Owner is sufficient for a personal
   course project; organizations should grant narrower administrative roles.

Alternatively, create and link the project with the Google Cloud CLI:

```bash
export PROJECT_ID="your-globally-unique-project-id"
export BILLING_ACCOUNT_ID="XXXXXX-XXXXXX-XXXXXX"

gcloud projects create "$PROJECT_ID" --name="SLM OCR Course"
gcloud billing accounts list
gcloud billing projects link "$PROJECT_ID" --billing-account="$BILLING_ACCOUNT_ID"
gcloud billing projects describe "$PROJECT_ID"
```

If the project already exists, skip `gcloud projects create`.

## 3. Authenticate and select the project

Terraform uses Application Default Credentials (ADC), which are separate from the
interactive `gcloud` login:

```bash
export PROJECT_ID="your-gcp-project-id"

gcloud auth login
gcloud auth application-default login
gcloud config set project "$PROJECT_ID"
gcloud config set billing/quota_project "$PROJECT_ID"

# Bootstrap the APIs Terraform needs in order to enable all remaining APIs.
gcloud services enable \
  serviceusage.googleapis.com \
  cloudresourcemanager.googleapis.com \
  --project "$PROJECT_ID"
```

Confirm both project and billing before spending money:

```bash
gcloud config get-value project
gcloud billing projects describe "$PROJECT_ID"
```

The billing output must contain `billingEnabled: true`.

## 4. Request GPU quota and choose a zone

Quota is regional and capacity is zonal. With the defaults, request in
`europe-west4`:

| Quota metric | Requested limit |
|---|---:|
| NVIDIA T4 GPUs | 4 |
| NVIDIA A100 80GB GPUs | 4 |

In **IAM & Admin → Quotas & System Limits**, filter by region and request each GPU type
separately. New paid accounts can still be denied until they establish billing history
or contact Google Cloud Sales.

Verify which zones advertise both accelerators:

```bash
gcloud compute accelerator-types list \
  --filter="zone ~ europe-west4 AND (name=nvidia-tesla-t4 OR name=nvidia-a100-80gb)" \
  --format="table(name,zone)" \
  --project "$PROJECT_ID"
```

The defaults use `europe-west4-a`. Accelerator listing and approved quota do not
guarantee live capacity; the GPU smoke test below is the definitive check.

## 5. Configure and apply Terraform

From the repository root:

```bash
cd infra-gke
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` and set `project_id`. If you choose another location, `zone`
must belong to `region` and must offer both target accelerators.

Initialize, review, and apply:

```bash
terraform fmt -check
terraform init
terraform validate
terraform plan -out=tfplan
terraform apply tfplan
```

GPU pools start at zero, so `terraform apply` does not immediately allocate a GPU.
Terraform state and `terraform.tfvars` are local and ignored by Git. For shared or
production use, configure a protected remote GCS backend before the first apply.

Configure `kubectl` using the exact Terraform outputs:

```bash
eval "$(terraform output -raw get_credentials_command)"
kubectl cluster-info
kubectl get nodes -L cloud.google.com/gke-nodepool,app,workload
kubectl get storageclass standard-rwx
```

Initially only the system, Redis, and API pools should have nodes. GPU nodes appear
when a matching GPU pod triggers the cluster autoscaler.

## 6. Prove both GPU pools and managed drivers work

The following helper checks one pool at a time. It triggers scale-up, runs
`nvidia-smi`, prints the result, and deletes the test pod. A100 allocation starts
billing as soon as its node boots.

```bash
smoke_gpu() {
  pool="$1"
  cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: gpu-smoke-${pool}
spec:
  restartPolicy: Never
  nodeSelector:
    cloud.google.com/gke-nodepool: ${pool}
  tolerations:
    - key: nvidia.com/gpu
      operator: Equal
      value: present
      effect: NoSchedule
  containers:
    - name: nvidia-smi
      image: nvidia/cuda:12.4.1-base-ubuntu22.04
      command: ["nvidia-smi"]
      resources:
        limits:
          nvidia.com/gpu: 1
EOF
  kubectl wait pod/gpu-smoke-${pool} \
    --for=jsonpath='{.status.phase}'=Succeeded --timeout=20m
  kubectl logs gpu-smoke-${pool}
  kubectl delete pod gpu-smoke-${pool}
}

smoke_gpu gpunpt4
smoke_gpu gpunpa100
```

If a pod remains Pending, inspect it and the autoscaler events:

```bash
kubectl describe pod gpu-smoke-gpunpt4
kubectl get events --sort-by=.lastTimestamp | tail -50
gcloud container operations list --filter="status!=DONE"
```

Typical causes are missing quota (`QUOTA_EXCEEDED`) or temporary zonal capacity
(`ZONE_RESOURCE_POOL_EXHAUSTED`).

## 7. Build and push the repository images

Return to the repository root and authenticate Docker to the Terraform-created
registry. `--platform linux/amd64` is important when building on Apple Silicon because
these GKE machine types are x86-64.

```bash
cd ..
export REGISTRY="$(cd infra-gke && terraform output -raw artifact_registry)"
gcloud auth configure-docker "${REGISTRY%%/*}"

docker build --platform linux/amd64 -t "$REGISTRY/ocr-vlm-qwen:latest" ./server
docker push "$REGISTRY/ocr-vlm-qwen:latest"

docker build --platform linux/amd64 -t "$REGISTRY/ocr-api-rust:latest" ./client_rt_producer
docker push "$REGISTRY/ocr-api-rust:latest"

docker build --platform linux/amd64 -t "$REGISTRY/ocr-worker-rt:latest" ./client_rt_consumer
docker push "$REGISTRY/ocr-worker-rt:latest"

gcloud artifacts docker images list "$REGISTRY"
```

## 8. Install cluster add-ons

KEDA scales the worker and vLLM Deployments. Prometheus supplies the vLLM queue metric
used by KEDA and Grafana provides dashboards.

```bash
helm repo add kedacore https://kedacore.github.io/charts
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

helm upgrade --install keda kedacore/keda \
  --namespace keda --create-namespace --wait

helm upgrade --install prometheus prometheus-community/kube-prometheus-stack \
  --namespace monitoring --create-namespace \
  --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false \
  --set grafana.enabled=true \
  --wait --timeout 15m
```

## 9. Provision model storage and ingest weights

The PVC manifest creates a `filestore-standard-rwx` StorageClass connected to the
cluster's custom VPC, then provisions a **1 TiB Filestore instance**. Filestore has a
significant minimum monthly cost and does not scale to zero with GPU pools. If you
changed `cluster_name` from `gke-ocr-cluster`, update the StorageClass `network` in
`pvc.yaml` to `${cluster_name}-network` before applying it.

```bash
kubectl apply -f k8s/gke/infra/provisioning/pvc.yaml
kubectl wait pvc/model-weights-pvc --for=jsonpath='{.status.phase}'=Bound --timeout=30m

kubectl apply -f k8s/gke/infra/provisioning/ingest-job.yaml
kubectl wait pod -l job-name=model-weight-ingest --for=condition=Ready --timeout=15m
kubectl logs -f job/model-weight-ingest
kubectl wait job/model-weight-ingest --for=condition=complete --timeout=60m
kubectl delete job model-weight-ingest
```

The public models currently need no Hugging Face token. If that changes, do not commit
a token to the Job manifest; create a Kubernetes Secret and reference it instead.

## 10. Deploy the OCR stack

The checked-in manifests contain a placeholder registry in `us-central1`. Render them
through Kustomize and replace the complete image prefix in the stream, leaving source
files unchanged:

```bash
export REGISTRY="$(cd infra-gke && terraform output -raw artifact_registry)"

kubectl kustomize k8s/gke \
  | sed "s|us-central1-docker.pkg.dev/<PROJECT_ID>/ocr-repository|${REGISTRY}|g" \
  | kubectl apply -f -
```

Watch the workloads and autoscaler. The A100 node can take several minutes to appear
and vLLM then needs time to initialize:

```bash
kubectl get pods -w
kubectl get nodes -L cloud.google.com/gke-nodepool
kubectl get scaledobjects
kubectl logs -f deployment/ocr-vlm-deployment
kubectl logs -f deployment/ocr-api-deployment
```

The OCR API uses an **internal** GCP load balancer. Test it locally with a port-forward:

```bash
kubectl rollout status deployment/ocr-api-deployment --timeout=10m
kubectl port-forward service/ocr-api-service 8080:80
```

In another terminal, use `http://localhost:8080` for API requests.

Check GPU resources and utilization:

```bash
kubectl get nodes \
  -o=custom-columns='NAME:.metadata.name,POOL:.metadata.labels.cloud\.google\.com/gke-nodepool,GPU:.status.allocatable.nvidia\.com/gpu'

kubectl port-forward -n monitoring service/prometheus-grafana 3000:80
kubectl get secret -n monitoring prometheus-grafana \
  -o jsonpath='{.data.admin-password}' | base64 --decode; echo
```

Grafana is then available at <http://localhost:3000> with user `admin`.

> The T4 pool is capped at four nodes, while the current worker KEDA object permits up
> to ten replicas. At most four GPU workers can run with the documented quota; extra
> replicas remain Pending. Increase both quota and `gpu_max_nodes`, or lower the KEDA
> maximum, if you need a different limit.

## 11. Cost controls and teardown

Before deploying GPUs, create Billing budget alerts at 50%, 80%, and 100%. GPU pools
scale to zero, but Filestore, the three CPU pools, the GKE control plane, and Artifact
Registry storage continue billing while they exist.

Delete Kubernetes resources first so the Filestore CSI controller can remove its
cloud resource cleanly, then destroy Terraform infrastructure:

```bash
# From the repository root.
kubectl delete -k k8s/gke --ignore-not-found
kubectl delete job model-weight-ingest --ignore-not-found
kubectl delete pvc model-weights-pvc --ignore-not-found

helm uninstall prometheus --namespace monitoring || true
helm uninstall keda --namespace keda || true

# Confirm the course Filestore instance is gone before deleting its VPC.
gcloud filestore instances list --project "$PROJECT_ID"

cd infra-gke
terraform plan -destroy -out=destroy.tfplan
terraform apply destroy.tfplan
```

Terraform intentionally leaves project APIs enabled and does not delete the Google
Cloud project or billing account. If this is a dedicated disposable project, deleting
the project after Terraform completes is the strongest final cost stop.

## Troubleshooting quick reference

| Symptom | Likely cause / check |
|---|---|
| Terraform cannot enable APIs | Run the bootstrap `gcloud services enable` command and verify IAM permissions. |
| GPU pool creation fails immediately | Regional GPU quota is still zero or too low. |
| GPU pod stays Pending | Inspect Pod events; quota or live zonal capacity is usually the cause. |
| Node exists but has no `nvidia.com/gpu` | Check GKE GPU driver installer pods in `kube-system` and node events. |
| PVC stays Pending or the Pod reports `FailedMount` | Confirm the Filestore API and CSI add-on, verify the StorageClass `network` matches the cluster VPC, then inspect `kubectl describe pvc` and `kubectl describe pod`. |
| Image pull is denied | Confirm the node service account has Artifact Registry Reader and the image prefix matches the Terraform output. |
| `terraform destroy` cannot remove the network | A Filestore instance or forwarding rule still uses the subnet; delete Kubernetes PVCs/load balancers and retry. |
