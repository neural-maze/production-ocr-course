#!/usr/bin/env python3
"""
Model Context Protocol (MCP) Server for The Neural Maze Document OCR & VLM Infrastructure.
Connects Antigravity CLI, Claude Code, and other AI coding assistants to the AKS-deployed OCR cluster.
Supports both local Stdio transport and in-cluster SSE transport.
"""

import os
import sys
import time
import base64
import asyncio
from typing import Optional, Dict, Any
import httpx
from mcp.server.fastmcp import FastMCP

# Configuration from Environment Variables
OCR_API_URL = os.getenv("OCR_API_URL", "http://localhost:5000")
APIM_SUBSCRIPTION_KEY = os.getenv("APIM_SUBSCRIPTION_KEY", "")
POLL_INTERVAL_SEC = float(os.getenv("POLL_INTERVAL_SEC", "0.5"))
MAX_TIMEOUT_SEC = float(os.getenv("MAX_TIMEOUT_SEC", "120.0"))
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

# Initialize FastMCP Server
mcp = FastMCP(
    name="neural-maze-ocr",
    instructions="High-performance visual document understanding & OCR tool connected to private AKS cluster.",
    host=HOST,
    port=PORT,
)


@mcp.tool()
async def parse_document(
    file_path: str,
    include_layout: bool = False,
) -> Dict[str, Any]:
    """
    Submits a document (image or PDF) to the private AKS OCR pipeline and returns structured Markdown.

    Args:
        file_path: Path to the image (PNG, JPG, WebP) or PDF document.
        include_layout: If True, includes bounding boxes and detected document regions in the output.

    Returns:
        A dictionary containing the markdown content, status, and optional layout metadata.
    """
    expanded_path = os.path.expanduser(file_path)
    if not os.path.exists(expanded_path):
        return {
            "success": False,
            "error": f"File not found: {file_path}",
        }

    headers = {}
    if APIM_SUBSCRIPTION_KEY:
        headers["Ocp-Apim-Subscription-Key"] = APIM_SUBSCRIPTION_KEY

    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. Submit Document Asynchronously
        try:
            with open(expanded_path, "rb") as f:
                files = {"file": (os.path.basename(expanded_path), f)}
                submit_url = f"{OCR_API_URL.rstrip('/')}/process"
                response = await client.post(submit_url, files=files, headers=headers)
                response.raise_for_status()
                task_data = response.json()
        except httpx.HTTPError as exc:
            return {
                "success": False,
                "error": f"Failed to submit task to OCR API ({OCR_API_URL}): {str(exc)}",
            }
        except Exception as exc:
            return {
                "success": False,
                "error": f"Unexpected submission error: {str(exc)}",
            }

        task_id = task_data.get("task_id")
        if not task_id:
            return {
                "success": False,
                "error": f"Invalid API response, missing task_id: {task_data}",
            }

        # 2. Poll for Task Completion
        status_url = f"{OCR_API_URL.rstrip('/')}/status/{task_id}"
        start_time = time.time()

        while (time.time() - start_time) < MAX_TIMEOUT_SEC:
            try:
                status_res = await client.get(status_url, headers=headers)
                if status_res.status_code == 200:
                    data = status_res.json()
                    status = data.get("status")

                    if status == "done":
                        result = data.get("result", {})
                        markdown = result.get("markdown", "")
                        layout = result.get("layout", {}) if include_layout else None
                        
                        response_payload = {
                            "success": True,
                            "task_id": task_id,
                            "markdown": markdown,
                            "elapsed_sec": round(time.time() - start_time, 2),
                        }
                        if include_layout:
                            response_payload["layout"] = layout

                        return response_payload

                    elif status == "failed":
                        return {
                            "success": False,
                            "task_id": task_id,
                            "error": data.get("error", "Unknown worker failure"),
                        }

                await asyncio.sleep(POLL_INTERVAL_SEC)
            except httpx.HTTPError as poll_exc:
                await asyncio.sleep(POLL_INTERVAL_SEC)

        return {
            "success": False,
            "task_id": task_id,
            "error": f"Task timed out after {MAX_TIMEOUT_SEC} seconds",
        }


if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "stdio").lower()
    if transport == "sse" or "--sse" in sys.argv:
        # Run FastMCP over SSE transport for in-cluster deployment
        mcp.run(transport="sse")
    else:
        # Run FastMCP over standard Stdio transport for local CLI execution
        mcp.run(transport="stdio")
