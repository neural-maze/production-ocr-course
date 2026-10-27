# Setting Up Your Azure Account

This is the very first step of the course: creating an Azure account and getting
the CLI installed. Azure is our primary cloud for the SLM-OCR pipeline. Once
this is done, move on to `azure_gpu_prereqs.md` to get GPU access and build the
cluster.

> This guide covers account creation only. It's all done in the browser — there
> are no commands until the CLI install at the end.

> 📸 For screenshots of every portal step below, see the companion Substack
> article, [_Week 0 — Cloud Setup_](https://theneuralmaze.substack.com/p/the-slm-ocr-course-week-0-bonus-cloud).

---

## 1. Create your Azure account (free trial)

1. Go to **https://azure.microsoft.com/free**.
2. Click **Start free**.
3. Sign in with a Microsoft account, or create a new one. If this is a fresh
   account, use an email you control long-term (this becomes your billing
   identity).
4. Fill in your profile and phone number for identity verification.
5. Add a **credit or debit card** for verification.
   > **This does not charge you.** Azure uses it only to confirm you're a real
   > person. Prepaid/virtual cards are usually rejected — use a normal card.
6. Accept the agreement and finish. You now have the free account with **$200
   in credit** and a set of always-free services.

> **What you get:** ~$200 credit valid for 30 days, plus 12 months of selected
> free services. This is enough to explore, **but not to run GPUs** — see the
> warning below.

---

## 2. Important things to know before you go further

**The free trial cannot run GPUs.** This is the single most important thing to
understand up front. Free/Trial subscriptions are **capped at 0 GPU quota** and
the increase button is disabled. To use the A100/T4 nodes this course needs,
you'll have to upgrade to **Pay-As-You-Go** first (you keep any remaining
credit). That whole process is covered in `azure_gpu_prereqs.md`.

**One free account per person.** Microsoft ties the free credit to your identity
and card. You can't farm multiple $200 grants.

**The 30-day clock is real.** The $200 credit expires 30 days after signup even
if unused. Don't create the account weeks before you're ready to build.

**Watch the directory/tenant.** A personal signup creates a directory like
`yourname.onmicrosoft.com`. That's normal. Just be aware which directory you're
in when you later look for your subscription.

---

## 3. Verify you can see your subscription

1. Go to **https://portal.azure.com**.
2. Search for **Subscriptions** in the top bar.
3. You should see one subscription (often named **"Azure subscription 1"**).
   Click it to confirm it's **Active**.

If you don't see it, use **Switch directory** (top-right) to make sure you're in
the directory your account created.

---

## 4. Install the Azure CLI (`az`)

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

**Verify and log in**
```bash
az version
az login                              # opens a browser; use --use-device-code if headless
az account list --output table
az account set --subscription "<YOUR_SUBSCRIPTION_ID>"
```

Install `kubectl` too (you'll need it for AKS later):
```bash
az aks install-cli
```

---

## ✅ Next step

Your account exists and the CLI works. Now head to **`azure_gpu_prereqs.md`** to
upgrade to Pay-As-You-Go, request GPU quota, verify availability, and confirm
capacity — then move on to **`aks_deployment.md`** to build the AKS cluster.
</content>
