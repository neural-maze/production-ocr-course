import sys
import time
from pathlib import Path

import httpx


def main() -> None:
    sample_path = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else Path("ocr-course/week4_rust_gateway_deployment/sample_image.png")
    )
    if not sample_path.exists():
        print(f"File not found: {sample_path}")
        sys.exit(1)

    base_url = "http://localhost:8000"
    print(f"🚀 Submitting {sample_path.name} to {base_url}/api/documents ...")

    with httpx.Client(timeout=60.0) as client:
        with sample_path.open("rb") as f:
            resp = client.post(
                f"{base_url}/api/documents",
                files={"file": (sample_path.name, f)},
                data={"batch_size": "4"},
            )
            resp.raise_for_status()
            submitted = resp.json()

        doc_id = submitted["document_id"]
        print(f"✅ Queued document_id={doc_id} (inngest_event_id={submitted.get('inngest_event_id')})")

        t0 = time.perf_counter()
        while True:
            doc_resp = client.get(f"{base_url}/api/documents/{doc_id}")
            doc_resp.raise_for_status()
            doc = doc_resp.json()
            status = doc["status"]
            step = doc["current_step"]
            elapsed = time.perf_counter() - t0
            print(f"⏳ [{elapsed:5.1f}s] status={status:<12} | {step}")

            if status == "done":
                metrics = doc["metrics"]
                print("\n" + "=" * 78)
                print(f"🎉 OCR Complete! ({doc['filename']} · {doc['total_pages']} page(s))")
                print("=" * 78)
                print(f"• Backend / Model   : {doc['backend']} ({doc['model_id']})")
                print(f"• Total Latency     : {metrics['total_latency_ms']:.0f} ms (Rasterize: {metrics['rasterize_ms']:.0f} ms, LLM: {metrics['llm_total_ms']:.0f} ms)")
                print(f"• Throughput        : {metrics['pages_per_sec']:.2f} pages/sec")
                print(f"• Prefill Speed     : {metrics['avg_prompt_tps']:.1f} tok/s ({metrics['prompt_tokens']} prompt tokens)")
                print(f"• Decode Speed      : {metrics['avg_generation_tps']:.1f} tok/s ({metrics['generation_tokens']} generated tokens)")
                print(f"• Peak Unified Mem  : {metrics['peak_memory_gb']:.2f} GB")
                print("-" * 78)
                print("📄 Extracted Markdown Preview:\n")
                print(doc["markdown"][:1500])
                print("=" * 78)
                break

            if status == "failed":
                print(f"\n❌ Processing failed: {doc.get('error')}")
                sys.exit(1)

            time.sleep(1.0)


if __name__ == "__main__":
    main()

