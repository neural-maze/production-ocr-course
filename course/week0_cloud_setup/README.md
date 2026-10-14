# Week 0: ☁️ Cloud Setup

> 📘 **Article:** [The SLM OCR Course, Week 0 (Bonus): Cloud Setup](https://theneuralmaze.substack.com/p/the-slm-ocr-course-week-0-bonus-cloud)

## 🎯 What You'll Learn

* How to create an Azure account and claim the free credits.
* Why free/trial subscriptions can't run GPUs, and how to upgrade to Pay-As-You-Go.
* How GPU quota works per VM family and region, and how to request it for the A100 and T4 families.

## 📖 Guides

1. [`docs/azure_onboarding.md`](../../docs/azure_onboarding.md): account, credits, Azure CLI.
2. [`docs/azure_gpu_prereqs.md`](../../docs/azure_gpu_prereqs.md): Pay-As-You-Go upgrade, quota requests, availability checks.

## 🛠️ Hands-On

From the repo root:

```bash
cp .env.example .env      # set SUBSCRIPTION_ID and a globally unique ACR_NAME
make check-cli            # az, kubectl, helm, curl, python3
make auth                 # select the subscription, register resource providers
make check-quota          # NC-family quota in LOCATION
```

## ✅ Deliverables

- [ ] `.env` filled in with your subscription ID and ACR name.
- [ ] `make check-cli` and `make auth` both succeed.
- [ ] `make check-quota` shows enough quota in your region for **1× `Standard_NC24ads_A100_v4`** (24 vCPUs) and **1× `Standard_NC16as_T4_v3`** (16 vCPUs).

---

**Next:** [Week 1: Kubernetes for AI Systems →](../week1_kubernetes_for_ai/README.md)
