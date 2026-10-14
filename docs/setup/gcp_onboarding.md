# Setting Up Your Google Cloud Account

This is the optional GCP track. The course is built on Azure, but if you want to
replicate the pipeline on Google Cloud (or just prefer GCP), start here. Once
this is done, move on to `gcp_gpu_prereqs.md` to get GPU access and build the
GKE cluster.

> This guide covers account creation only. It's all done in the browser — there
> are no commands until the CLI install at the end.

---

## 1. Create your Google Cloud account (free trial)

1. Go to **https://cloud.google.com/free**.
2. Click **Get started for free**.
   > _[PLACEHOLDER: screenshot of the "Get started for free" button ➜]_
3. Sign in with a Google account (or create one).
4. Fill in your country and accept the terms.
5. Add a **credit or debit card** for verification.
   > **This does not charge you** during the trial. Google uses it to confirm
   > identity. You won't be billed unless you manually upgrade to a paid account.
   > _[PLACEHOLDER: screenshot of the payment/verification step ➜]_
6. Finish. You now have **$300 in credit valid for 90 days**.

> **What you get:** $300 credit for 90 days, plus some always-free services.
> More generous than Azure's trial — but, same as Azure, **it won't run GPUs**
> until you activate a full account.

---

## 2. Important things to know before you go further

**The free trial cannot run GPUs.** Just like Azure: GPU quota is capped at 0 on
the trial, and the increase button shows *"not eligible"* / *"between 0 and 0"*.
You must **activate the full (paid) account** first — you keep the $300 credit.
Covered in `gcp_gpu_prereqs.md`.

**New paid accounts may still be refused GPU quota.** Even after activating, a
brand-new project with no spending history can be denied GPU quota as an
anti-fraud measure. If that happens, build up a little ordinary usage first, or
contact Google Cloud Sales to unlock it.

**GCP has no L40/L40S.** If you're mapping the course's hardware over from
Azure: use **T4** (N1) for the light pool and **A100** (A2) for the heavy pool
— same GPU as Azure, for architectural parity. L4 (G2) is a viable upgrade if
you want to deviate. Details in `gcp_gpu_prereqs.md`.

**Create a project.** Unlike Azure, GCP organizes everything under **projects**.
Create one (e.g. `slm-ocr-course`) right after signup and use it throughout.

---

## 3. Create your project

1. Go to **https://console.cloud.google.com**.
2. Top bar → project dropdown → **New Project**.
   > _[PLACEHOLDER: screenshot of the New Project dropdown ➜]_
3. Name it (e.g. `slm-ocr-course`) and create it.
4. Make sure it's selected in the top bar before doing anything else.

---

## 4. Install the gcloud CLI

**macOS (Homebrew)**
```bash
brew install --cask google-cloud-sdk
```

**Linux (Debian/Ubuntu)**
```bash
curl https://sdk.cloud.google.com | bash && exec -l $SHELL
```

**Windows**: download the installer from
**https://cloud.google.com/sdk/docs/install**

**Install kubectl + the GKE auth plugin** (needed for GKE later)
```bash
gcloud components install kubectl gke-gcloud-auth-plugin
```

**Log in and select your project**
```bash
gcloud auth login
gcloud config set project <YOUR_PROJECT_ID>
gcloud config get-value project
```

---

## ✅ Next step

Your account and project exist and the CLI works. Now head to
**`gcp_gpu_prereqs.md`** to link billing, activate the full account, request GPU
quota, and verify availability by zone — then move on to **`gke_deployment.md`**
to build the GKE cluster.
</content>
