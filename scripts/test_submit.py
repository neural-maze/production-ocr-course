"""Submit a document to the Rust Producer and poll until the async OCR task completes.

Usage: python3 scripts/test_submit.py [FILE] [--api-url http://localhost:5000] [--timeout 600]
"""

import argparse
import json
import subprocess
import sys
import time


def curl_json(*args: str) -> dict:
    return json.loads(subprocess.check_output(["curl", "-sS", *args]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", nargs="?", default="samples/sample_image.png")
    parser.add_argument("--api-url", default="http://localhost:5000")
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()

    api = args.api_url.rstrip("/")
    print(f"🧪 Submitting {args.file} to {api}/process ...")
    task = curl_json("-X", "POST", f"{api}/process", "-F", f"file=@{args.file}")
    task_id = task["task_id"]
    print(f"🚀 Task submitted: {task_id} (status: {task['status']})")

    start = time.time()
    while time.time() - start < args.timeout:
        status = curl_json(f"{api}/status/{task_id}")
        print(f"⏳ status: {status['status']} (elapsed: {time.time() - start:.1f}s)")
        if status["status"] == "done":
            print("✅ OCR complete!\n")
            print(status["result"]["markdown"][:1000] + "...")
            return 0
        if status["status"] == "failed":
            print(f"❌ Task failed: {status.get('error')}")
            return 1
        time.sleep(0.5)

    # GPU pools scale from zero, so the first request can take several minutes.
    print(f"❌ Timed out after {args.timeout:.0f}s waiting for task {task_id}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
