# GPU Deployment on Google Cloud (GKE)

This guide takes you from zero to a GPU-ready GKE cluster for the OCR pipeline.
Same **asymmetric hardware** idea as the Azure guide: cheap **T4** nodes for
orchestration and layout extraction, premium **A100** nodes for the heavy VLM
generation — kept on T4 (not the newer L4) so the two clouds stay
architecturally comparable (see `cloud_comparison.md`). Read this fully before
spending money.

---

## 1. Target machine types

| Role                          | Machine type       | GPU                          | vCPUs/VM |
|-------------------------------|--------------------|------------------------------|----------|
| Layout workers + orchestrator | `n1-standard-4`    | 1× NVIDIA T4 (16 GB)         | 4        |
| VLM inference                 | `a2-ultragpu-1g`   | 1× NVIDIA A100 80 GB         | 12       |

The T4 is attached as an accelerator on `n1-standard-4`
(`--accelerator type=nvidia-tesla-t4,count=1`) rather than a fixed T4 SKU like
Azure's — that's the `gpunpt4` node pool built in `gke_deployment.md`.

> **If you'd rather use L4 instead of T4:** GCP's modern inference card is the
> **L4** (`g2-standard-16`, 24 GB VRAM, Ada Lovelace, FP8 support) — cheap,
> widely available, and strong enough to skip the A100 pool entirely for some
> models. It's a reasonable substitution, but it changes the node pool from
> what `gke_deployment.md` builds (see the callout in Section 7 for the
> equivalent commands), so only go this route if you're prepared to adapt that
> guide's manifests/`nodeSelector`s accordingly.
>
> GCP does **not** offer L40 / L40S. Its lineup is T4/L4 (light), A100 (A2,
> heavy), H100 (A3), B200 (A4), and RTX PRO 6000 (G4, 96 GB). For A100 80 GB
> use `a2-ultragpu-1g`; for A100 40 GB use `a2-highgpu-1g`
> (`nvidia-tesla-a100`).

## 2. Quota to request (per region)

On GCP, GPU quota is measured in **GPU count**, not vCPUs — simpler than Azure.

| Quota metric              | Request | Notes                          |
|----------------------------|---------|--------------------------------|
| `NVIDIA T4 GPUs`          | 4       | Matches the 4-node max on the `gpunpt4` pool in `gke_deployment.md` |
| `NVIDIA A100 80GB GPUs`   | 4       | Matches the 4-node max on the `gpunpa100` pool. Use `NVIDIA A100 GPUs` for 40 GB |

> Both start at **0** on a new project. Going the L4 route instead? Request
> `NVIDIA L4 GPUs` → 4 in place of the T4 row.

---

## 3. Install the gcloud CLI

**macOS (Homebrew)**
```bash
brew install --cask google-cloud-sdk
```

**Linux (Debian/Ubuntu)**
```bash
curl https://sdk.cloud.google.com | bash && exec -l $SHELL
```

**Windows**: download the installer from
https://cloud.google.com/sdk/docs/install

**Install kubectl + the GKE auth plugin**
```bash
gcloud components install kubectl gke-gcloud-auth-plugin
```

**Log in and set your project**
```bash
gcloud auth login
gcloud config set project <YOUR_PROJECT_ID>
gcloud config get-value project
```

---

## 4. Enable billing, then APIs

GPU requires a project with **billing enabled** and a **fully activated**
(non-trial) account.

```bash
# Link a billing account (get the ID from: gcloud billing accounts list)
gcloud billing projects link <YOUR_PROJECT_ID> \
  --billing-account=XXXXXX-XXXXXX-XXXXXX

gcloud billing projects describe <YOUR_PROJECT_ID>   # billingEnabled: true

# Enable required services
gcloud services enable container.googleapis.com compute.googleapis.com \
  artifactregistry.googleapis.com
```

If `gcloud services enable` fails with `UREQ_PROJECT_BILLING_NOT_FOUND`, billing
isn't linked yet — fix step above first.

---

## 5. Get GPU access (the trial trap)

If the quota page shows **"between 0 and 0"** and *"not eligible for a quota
increase"*, your project is still on the **Free Trial** profile. GCP blocks all
GPU quota increases on trial accounts.

1. **Activate the full account**: https://console.cloud.google.com/billing →
   **Activate** / **Upgrade** (keeps your $300 credit, moves you to paid).
2. Wait a few minutes for the change to propagate, then retry the increase.
3. If it's still blocked after activation, GCP sometimes withholds GPU quota
   from brand-new paid accounts with no usage history. Options: generate some
   ordinary (CPU) usage first and retry in a few days, or **contact Sales**
   (the link shown in the quota dialog) to unlock it manually.

**Request the increase** in *IAM & Admin → Quotas & System Limits*, filter by
region, select the metric, and use this justification (one per GPU type):

> Deploying an OCR / document-intelligence pipeline on GKE in europe-west4-a.
> The NVIDIA T4 node pool handles layout extraction with cluster autoscaling
> and scale-to-zero for cost efficiency. Requesting 4 T4 GPUs.

Keep it short. Request GPU types in separate tickets — T4 usually clears fast,
A100 can take longer.

---

## 6. Choose a region/zone and verify availability

GPU availability varies **by zone within a region**, so check the exact zones.

```bash
gcloud compute accelerator-types list \
  --filter="zone ~ europe-west4" \
  --format="table(name, zone)"
```

Look for `nvidia-tesla-t4` (or `nvidia-l4` if you're going that route) and
`nvidia-a100-80gb` (or `nvidia-tesla-a100` for 40 GB) and pick a zone that has
**both**. For example, in `europe-west4` only `europe-west4-a` has T4 **and**
A100 80 GB together (`-b` has 40 GB only, `-c` has no A100). Confirm for your
own region before choosing.

> Same **quota ≠ capacity** rule as Azure: the type appearing here means it
> exists in that zone, not that it's free at deploy time.

---

## 7. Capacity-proof cluster (disposable — not the real deployment)

This proves one level up from the zone check above: that **GKE itself** can
actually schedule pods onto both GPU types via the cluster autoscaler, before
you invest time in the full build. This cluster is throwaway — **delete it at
the end of this section.** The real, production cluster (Artifact Registry,
model ingestion, Redis, the API gateway, KEDA, the works) is built fresh in
[`gke_deployment.md`](gke_deployment.md) and does not reuse anything created
here.

One node pool per GPU type, both autoscaling with **scale-to-zero**
(`--min-nodes=0`). `gpu-driver-version=default` makes GKE install the NVIDIA
drivers automatically.

```bash
CLUSTER=ocr-smoketest-cluster
ZONE=europe-west4-a

# Base cluster (no GPU)
gcloud container clusters create $CLUSTER \
  --zone $ZONE --num-nodes=1 --machine-type=e2-standard-4 \
  --release-channel=regular

# T4 worker pool
gcloud container node-pools create t4-workers \
  --cluster=$CLUSTER --zone=$ZONE \
  --machine-type=n1-standard-4 \
  --accelerator=type=nvidia-tesla-t4,count=1,gpu-driver-version=default \
  --enable-autoscaling --min-nodes=0 --max-nodes=4 \
  --node-labels=workload=layout

# A100 80GB inference pool
gcloud container node-pools create a100-inference \
  --cluster=$CLUSTER --zone=$ZONE \
  --machine-type=a2-ultragpu-1g \
  --accelerator=type=nvidia-a100-80gb,count=1,gpu-driver-version=default \
  --enable-autoscaling --min-nodes=0 --max-nodes=4 \
  --node-labels=workload=inference

# Connect kubectl
gcloud container clusters get-credentials $CLUSTER --zone $ZONE
kubectl get nodes
```

> **Going the L4 route instead?** Swap the T4 pool for:
> ```bash
> gcloud container node-pools create l4-workers \
>   --cluster=$CLUSTER --zone=$ZONE \
>   --machine-type=g2-standard-16 \
>   --accelerator=type=nvidia-l4,count=1,gpu-driver-version=default \
>   --enable-autoscaling --min-nodes=0 --max-nodes=4 \
>   --node-labels=workload=layout --spot
> ```

Target each workload to its pool with a `nodeSelector` (`workload: layout` /
`workload: inference`) and request `nvidia.com/gpu: 1`.

> Both pools scale up to 4 GPUs (`--max-nodes=4`), matching the requested
> quota. Keep `--min-nodes=0` for scale-to-zero.

Once you've confirmed `kubectl get nodes` shows both pools scheduling GPU pods
correctly, **tear the whole thing down** — this cluster has done its job:

```bash
gcloud container clusters delete $CLUSTER --zone $ZONE --quiet
```

---

## 8. Cost safety (before your first A100 boots)

- Set a **Budget + alerts** in *Billing → Budgets & alerts* (50/80/100 %).
- Keep **scale-to-zero** on both pools.
- For interruptible batch OCR, consider `--spot` on either pool — 70-90%
  cheaper, but reclaimed with short notice, so only for checkpoint-safe work.

---

## Appendix — Azure ↔ GCP quick map

| Concept            | Azure                              | GCP                              |
|---------------------|--------------------------------------|-------------------------------------|
| Light GPU          | T4 (`NC16as_T4_v3`)                | T4 (`n1-standard-4` + accelerator) |
| Heavy GPU          | A100 80GB (`NC24ads_A100_v4`)      | A100 80GB (`a2-ultragpu-1g`)     |
| Quota unit         | vCPUs per family                   | GPU count                        |
| Managed K8s        | AKS                                | GKE                              |
| Driver install     | NVIDIA device plugin (manual)      | `gpu-driver-version=default`     |
| Trial blocks GPU   | Yes → upgrade to Pay-As-You-Go     | Yes → activate full account      |

---

## ✅ Next step

Quota is approved, capacity is confirmed, and the disposable smoke-test
cluster from Section 7 is deleted. Head to
[`gke_deployment.md`](gke_deployment.md) to build the real 4-node-pool GKE
cluster (`gpunpt4`, `gpunpa100`, `redisnp`, `apinp`) and deploy the pipeline.
