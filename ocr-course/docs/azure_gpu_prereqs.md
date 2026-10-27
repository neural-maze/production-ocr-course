# GPU Deployment on Azure (AKS)

This guide takes you from zero to a GPU-ready AKS cluster for the OCR pipeline.
The architecture uses **asymmetric hardware**: cheap **T4** nodes for
orchestration and layout extraction, premium **A100** nodes for the heavy VLM
generation. Read this fully before spending money.

> 📸 For a click-by-click walkthrough with screenshots of every portal step
> below, see the companion Substack article,
> [_Week 0 — Cloud Setup_](https://theneuralmaze.substack.com/p/the-slm-ocr-course-week-0-bonus-cloud).

---

## 1. Target SKUs

| Role                          | SKU                        | GPU                 | vCPUs/VM |
|-------------------------------|----------------------------|---------------------|----------|
| Layout workers + orchestrator | `Standard_NC16as_T4_v3`    | 1× NVIDIA T4        | 16       |
| VLM inference                 | `Standard_NC24ads_A100_v4` | 1× NVIDIA A100 80GB | 24       |

The T4 workers run at 16 vCPU (not NC4/NC8) so the orchestrator has enough CPU
to slice each document and dispatch many crops to the A100 without becoming the
bottleneck.

## 2. Quota to request (per region)

Azure groups its VMs into **families** (all the `NCADS_A100_v4` sizes are one
family, all the `NCASv3_T4` sizes are another) and measures quota in **vCPUs**,
not GPU count. That's why the quota rows are named `... Family vCPUs`: each one
is *"the total vCPUs you may run across that VM family."* So you don't request
"4 GPUs"; you request the vCPUs that 4 of those VMs add up to.

| Quota family                        | Request | Why                                       |
|-------------------------------------|---------|-------------------------------------------|
| `Standard NCASv3_T4 Family vCPUs`   | 64      | 4 × NC16as_T4_v3 (16 vCPU) → 4 T4 GPUs    |
| `Standard NCADS_A100_v4 Family vCPUs` | 96    | 4 × NC24ads_A100_v4 (24 vCPU) → 4 A100 GPUs |

> ⚠️ **Don't confuse *family* with *GPU model*.** Several families have "A100"
> in the name. You want the **NC** family (`NCADS_A100_v4`, cost-optimized
> inference), **not** the `ND...A100` families (`NDAMSv4_A100`, `NDASv4_A100`),
> which are the ND series for distributed multi-GPU training and aren't needed
> here. Both quotas start at **0** on a new subscription.

---

## 3. Install the Azure CLI (`az`)

**macOS (Homebrew)**
```bash
brew update && brew install azure-cli
```

**Windows**
```powershell
winget install --exact --id Microsoft.AzureCLI
# or download the MSI: https://aka.ms/installazurecliwindows
```

**Linux (Debian/Ubuntu)**
```bash
curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash
```

**Linux (RHEL/Fedora/CentOS)**
```bash
sudo dnf install -y azure-cli
```

**Log in and select your subscription**
```bash
az version
az login                              # opens a browser; use --use-device-code if headless
az account list --output table
az account set --subscription "<YOUR_SUBSCRIPTION_ID>"
```

Also install `kubectl` (for AKS):
```bash
az aks install-cli
```

---

## 4. Get GPU access, step by step

Free/Trial subscriptions **cannot request quota increases**. If the quota page
says *"not eligible for quota adjustment"* or *"between 0 and 0"*, you're on a
free subscription and must move to Pay-As-You-Go first. This section walks
through the whole flow, click by click.

### 4.1 Register the Compute resource provider

Before any GPU quota is even visible, your subscription must have the
`Microsoft.Compute` provider registered.

1. In the portal, search for **Subscriptions** and open your subscription.
2. In the left menu, scroll down to **Settings → Resource providers**.
3. In the **Filter by name** box type `Microsoft.Compute`, select the
   **Microsoft.Compute** row, and click **Register** (top bar). Wait until its
   status flips from *NotRegistered* to *Registered* (1–2 min).

   CLI equivalent:
   ```bash
   az provider register --namespace Microsoft.Compute
   ```

### 4.2 Upgrade to Pay-As-You-Go (leave the free trial)

The free trial caps GPU quota at 0. Upgrade to unlock quota requests — you keep
any remaining credit.

1. From the portal **Home**, look for the credit banner at the top:
   **"$200 in credits remaining"** with an **Upgrade to pay-as-you-go** button.
   Click it. (A real credit/debit card is required; prepaid/virtual cards are
   usually rejected.)
2. Follow the prompts. When it's done you'll see a **"You've upgraded"**
   confirmation screen with cost-management recommendations.

> If the portal keeps behaving like a trial afterward, sign out / switch
> directory to force a refresh. No Upgrade button at all? Open a Billing support
> request or create a fresh Pay-As-You-Go subscription.

### 4.3 Open your subscription's Usage + quotas

1. Search **Subscriptions** in the top bar and open it.
2. Click your subscription (here it's renamed **Azure SLM OCR Course**).
3. In the left menu open **Settings → Usage + quotas**.

### 4.4 Request the T4 quota

1. At the top, set **Provider: Compute** and **Region: France Central** (or your
   chosen region). In the search box type `NCASv3_T4`.
2. You'll see **Standard NCASv3_T4 Family vCPUs** with a limit of `0 of 0`.
   Tick its checkbox.
3. Click the pencil / **New Quota Request**, enter **New limit = 64**, and
   submit.

### 4.5 Request the A100 quota

Repeat 4.4 for the A100:

1. Clear the search and type `NCADS_A100_v4`.
2. Select **Standard NCADS_A100_v4 Family vCPUs** → **New limit = 96** → submit.

> ⚠️ Pick `NCADS_A100_v4` (the **NC** family), **not** the `ND...A100` families —
> those are the ND series for distributed multi-GPU training, which you don't
> need here.

### 4.6 If it says "unable to adjust your quota"

GPU quota is almost never auto-approved. You'll usually be told to **submit a
support ticket** — this is normal, not an error. Click through to the ticket and
paste a short justification:

> Deploying an event-driven OCR / VLM inference pipeline on AKS. NVIDIA T4
> (NC16as_T4_v3) nodes for layout/orchestration and A100 (NC24ads_A100_v4) nodes
> for inference, each pool autoscaling with scale-to-zero. Region: France
> Central. Requesting 64 vCPU for NCASv3_T4 and 96 vCPU for NCADS_A100_v4.

Request the two families as **separate tickets** — T4 usually clears in hours;
A100 can take hours to a couple of days.

---

## 5. Choose a region and verify availability

GPU quota is per region, so pick one region and stick to it. For a global
audience, choose the closest region that offers **both** T4 and A100.

**Scan several regions at once:**
```bash
for LOC in francecentral swedencentral eastus2 southcentralus japaneast southeastasia; do
  echo "=== $LOC ==="
  az vm list-skus --location $LOC --all \
    --resource-type virtualMachines \
    --query "[?name=='Standard_NC24ads_A100_v4' || name=='Standard_NC16as_T4_v3'].{Name:name, Restr:restrictions[0].reasonCode}" \
    -o table
done
```
Pick the first region where **both** rows come back with an empty `Restr`.
`NotAvailableForSubscription` means that region won't serve the SKU to you.

**Check your current quota vs limit:**
```bash
az vm list-usage --location francecentral \
  --query "[?contains(localName,'A100') || contains(localName,'T4')].{Family:localName, Current:currentValue, Limit:limit}" \
  -o table
```

> **Quota ≠ capacity.** An empty `Restr` means the SKU is *offered* to your
> subscription — it does not read live inventory. For T4 it's nearly always
> safe; for the scarce A100 it's a strong hint, not a promise.

---

## 6. Smoke test: deploy one GPU VM, confirm it boots, then delete it

The only sure test is to deploy a single node. **A100 bills by the second —
delete it the moment the test passes.**

```bash
LOC=francecentral
RG=gpu-smoketest-rg

az group create --name $RG --location $LOC

# Swap the size for Standard_NC16as_T4_v3 to test the T4 instead
az vm create \
  --resource-group $RG \
  --name gpu-smoketest \
  --location $LOC \
  --size Standard_NC24ads_A100_v4 \
  --image Ubuntu2204 \
  --admin-username azureuser \
  --generate-ssh-keys \
  --public-ip-sku Standard

az vm get-instance-view \
  --resource-group $RG --name gpu-smoketest \
  --query "instanceView.statuses[?starts_with(code,'PowerState')].displayStatus" \
  -o tsv
# Expected: VM running

# DELETE EVERYTHING
az group delete --name $RG --yes --no-wait
az group exists --name $RG        # should eventually print: false
```

- `VM running` → capacity is real; proceed.
- `AllocationFailed` / `SkuNotAvailable` / `ZonalAllocationFailed` → no free
  GPU right now; try a fallback region. Not your fault.

The deploy takes ~2–5 min with capacity; capacity errors usually fail in 1–2 min.
A quota error (`QuotaExceeded`) fails instantly and means the quota isn't
approved yet — different problem from capacity.

---

## 7. Capacity-proof cluster (disposable — not the real deployment)

The VM smoke test in Section 6 proves the *SKU* boots. This step proves one
level up: that **AKS itself** can actually schedule pods onto both GPU types
via the cluster autoscaler and taints, before you invest time in the full
build. This cluster is throwaway — **delete it at the end of this section.**
The real, production cluster (ACR, model ingestion, Redis, the API gateway,
KEDA, the works) is built fresh in
[`aks_deployment.md`](aks_deployment.md) and does not reuse anything created
here.

Deploy **without a fixed zone** to maximize the chance of finding capacity
(match the conditions of the smoke test). One node pool per GPU type, both
autoscaling with **scale-to-zero** (`--min-count 0`).

```bash
RG=ocr-smoketest-aks-rg
LOC=francecentral
CLUSTER=ocr-smoketest-cluster

az group create --name $RG --location $LOC

# Base cluster (no GPU, for the control plane + light services)
az aks create \
  --resource-group $RG --name $CLUSTER \
  --location $LOC \
  --node-count 1 --node-vm-size Standard_D4s_v5 \
  --generate-ssh-keys

# T4 worker pool
az aks nodepool add \
  --resource-group $RG --cluster-name $CLUSTER \
  --name t4pool \
  --node-vm-size Standard_NC16as_T4_v3 \
  --enable-cluster-autoscaler --min-count 0 --max-count 4 \
  --labels workload=layout \
  --node-taints nvidia.com/gpu=present:NoSchedule

# A100 inference pool
az aks nodepool add \
  --resource-group $RG --cluster-name $CLUSTER \
  --name a100pool \
  --node-vm-size Standard_NC24ads_A100_v4 \
  --enable-cluster-autoscaler --min-count 0 --max-count 4 \
  --labels workload=inference \
  --node-taints nvidia.com/gpu=present:NoSchedule

# Connect kubectl
az aks get-credentials --resource-group $RG --name $CLUSTER
kubectl get nodes
```

Install the NVIDIA device plugin so pods can request `nvidia.com/gpu`:
```bash
kubectl apply -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.16.2/deployments/static/nvidia-device-plugin.yml
```

Target each workload to its pool with a `nodeSelector` (`workload: layout` /
`workload: inference`), tolerate the GPU taint, and request `nvidia.com/gpu: 1`.

> Both pools scale up to 4 GPUs (`--max-count 4`), matching the 64/96 vCPU
> quota granted for `NCASv3_T4` and `NCADS_A100_v4`. Keep `--min-count 0` so
> idle GPUs cost nothing.

Once you've confirmed `kubectl get nodes` shows both pools scheduling GPU pods
correctly, **tear the whole thing down** — this cluster has done its job:

```bash
az group delete --name $RG --yes --no-wait
```

---

## 8. Cost safety (before your first A100 boots)

- Set a **Budget + alerts**: *Cost Management + Billing → Budgets* → 50/80/100 %.
- Keep **scale-to-zero** on both pools.
- For interruptible batch OCR, consider **Azure Spot** node pools (add
  `--priority Spot --eviction-policy Delete`) — 70–82 % cheaper, but reclaimed
  with <30 s notice, so only for checkpoint-safe work.

---

## ✅ Next step

Quota is approved, capacity is confirmed, and the disposable smoke-test
cluster from Section 7 is torn down. Head to
[`aks_deployment.md`](aks_deployment.md) to build the real 4-node-pool AKS
cluster (`gpunpt4`, `gpunpa100`, `redisnp`, `apinp`) and deploy the pipeline.
