# Week 1: ⛵ Kubernetes for AI Systems

> 📘 **Article:** _link coming soon_

<p align="center"><img src="../../images/week1_infra_overview.png" width="600" alt="Week 1"></p>

## 🎯 What You'll Learn

* Pods, Deployments and Services, and how they map onto an ML serving system.
* Why the pipeline uses **three isolated node pools** (CPU, T4, A100) instead of one big pool.
* How **taints, tolerations and nodeSelectors** pin each workload to the right hardware.
* How AKS provisions GPU drivers (`EnableManagedGPUExperience`) and exposes `nvidia.com/gpu`.

## 🔍 Where It Lives

| File | What to look at |
| :--- | :--- |
| [`Makefile`](../../Makefile) | `infra`, `nodepools`, the `add-nodepool` macro (taints, autoscaler bounds) |
| [`deploy/k8s/apps/deployment-api.yml`](../../deploy/k8s/apps/deployment-api.yml) | `nodeSelector` + `tolerations` for the CPU and T4 workloads |
| [`deploy/k8s/apps/deployment-vlm.yml`](../../deploy/k8s/apps/deployment-vlm.yml) | GPU requests/limits and A100 placement |

## 🛠️ Hands-On

```bash
make infra          # resource group, ACR, AKS (blob CSI driver, managed identity)
make nodepools      # cpunp, gpunpt4, gpunpa100, each tainted sku=<name>:NoSchedule
make verify-gpu     # allocatable nvidia.com/gpu per node
kubectl get nodes -L kubernetes.azure.com/agentpool
```

## ✅ Deliverables

- [ ] `kubectl get nodes` lists nodes from the system pool plus `cpunp` (GPU pools may be at 0 nodes, which is expected).
- [ ] `kubectl describe node <a cpunp node>` shows the `sku=cpunp:NoSchedule` taint.
- [ ] `make verify-gpu` shows `GPU_ALLOCATABLE: 1` once a GPU node is up.
- [ ] A short note (3–5 sentences): what would happen to the A100 bill if the Redis pod had no `nodeSelector` and no taint kept it off the GPU pool?

---

**← Previous:** [Week 0](../week0_cloud_setup/README.md) · **Next:** [Week 2: SOTA OCR Approaches →](../week2_ocr_approaches/README.md)
