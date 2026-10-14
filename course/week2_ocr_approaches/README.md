# Week 2: 🧠 SOTA OCR Approaches & Visual Document Understanding

> 📘 **Article:** _link coming soon_

<p align="center"><img src="../../images/week2_ocr_approaches.png" width="600" alt="Week 2"></p>

## 🎯 What You'll Learn

* The landscape: classic OCR vs. single-stage end-to-end VLMs vs. **two-stage layout-first** pipelines.
* Why a deterministic layout detector (**PP-DocLayoutV3**) in front of a generative SLM (**Qwen 3.5**) improves fidelity on real-world scans.
* How the **GLM-OCR SDK** orchestrates page loading, layout detection, region-level prompting and result formatting.

## 🔍 Where It Lives

| File | What to look at |
| :--- | :--- |
| [`services/client_rt_consumer/config.yaml`](../../services/client_rt_consumer/config.yaml) | `pipeline.page_loader`, `pipeline.layout`, `pipeline.ocr_api`, `pipeline.result_formatter`, and the per-region prompts (`image_desc`, …) |
| [`docs/architecture.md`](../../docs/architecture.md) | *The Exact Document Workflow* and *Why a Pre-layout Encoder Improves Fidelity* |

## 🛠️ Hands-On

No cluster work this week. Read the config with the architecture doc open next to it, and trace a single page through each stage.

## ✅ Deliverables

- [ ] A diagram or bullet list mapping each stage of *The Exact Document Workflow* to the `config.yaml` section that controls it.
- [ ] One paragraph: for which document types would a single-stage model (see the [Unlimited-OCR lab](../labs/week3_unlimited_ocr_vllm/README.md)) be good enough, and where does layout-first win?
- [ ] Explain what `layout.batch_size` and `layout.workers` trade off on the T4 node.

---

**← Previous:** [Week 1](../week1_kubernetes_for_ai/README.md) · **Next:** [Week 3: vLLM Inference Engine →](../week3_vllm_inference/README.md)
