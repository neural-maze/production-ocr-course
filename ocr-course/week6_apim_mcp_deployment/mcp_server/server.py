#!/usr/bin/env python3
"""
Model Context Protocol (MCP) Server for The Neural Maze Document OCR & VLM Infrastructure.
Connects Antigravity CLI, Claude Code, and other AI coding assistants to the AKS-deployed OCR cluster.
Supports both local Stdio transport and in-cluster SSE transport.
"""

import os
import sys
import pathlib
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

# Root that documents must live under. Every path the model supplies is
# resolved against this and rejected if it escapes.
WORKSPACE = pathlib.Path(os.getenv("OCR_WORKSPACE", ".")).resolve()


def _resolve(file_path: str) -> pathlib.Path:
    """Resolve a path against the workspace and refuse anything outside it."""
    target = (WORKSPACE / file_path).resolve()
    if not target.is_relative_to(WORKSPACE):
        raise ValueError(f"path outside workspace: {file_path}")
    if not target.is_file():
        raise ValueError(f"no such file: {file_path}")
    return target


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
        file_path: Path to the image (PNG, JPG, WebP) or PDF document,
            relative to the workspace root. Paths outside it are rejected.
        include_layout: If True, includes bounding boxes and detected document regions in the output.

    Returns:
        A dictionary containing the markdown content, status, and optional layout metadata.
    """
    try:
        target = _resolve(file_path)
    except ValueError as exc:
        return {
            "success": False,
            "error": str(exc),
        }

    headers = {}
    if APIM_SUBSCRIPTION_KEY:
        headers["Ocp-Apim-Subscription-Key"] = APIM_SUBSCRIPTION_KEY

    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. Submit Document Asynchronously
        try:
            with open(target, "rb") as f:
                files = {"file": (target.name, f)}
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
