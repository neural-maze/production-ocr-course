# TNM OCR Model Context Protocol (MCP) Server

This MCP server bridges AI coding assistants (**Antigravity CLI `agy`**, **Claude Code**, **Antigravity IDE**, **Cursor**) directly to your private AKS-deployed OCR & VLM cluster.

---

## 🚀 Quick Setup

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Environment Variables
- `OCR_API_URL`: Base URL of your Rust Producer Gateway (e.g. `http://localhost:5000` via port-forward or `https://apim-ocr-service.azure-api.net/ocr` via APIM).
- `APIM_SUBSCRIPTION_KEY`: (Optional) Your Azure APIM subscription key if connecting through the enterprise perimeter.

---

## 🔌 Connecting to AI Assistants

### Antigravity CLI (`agy`) & Antigravity IDE
Add the server definition to `~/.gemini/config/mcp_config.json` (or `.agents/mcp_config.json` in your repository root):

```json
{
  "mcpServers": {
    "tnm-ocr": {
      "command": "python3",
      "args": ["/Users/yourname/path/to/deployment/mcp_server/server.py"],
      "env": {
        "OCR_API_URL": "http://localhost:5000"
      }
    }
  }
}
```

### Claude Code CLI
Register the server in Claude Code with a single command:

```bash
claude mcp add tnm-ocr -- python3 $(pwd)/server.py
```

### Cursor / VS Code (Cline / Roo Code)
Add to `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "tnm-ocr": {
      "command": "python3",
      "args": ["/absolute/path/to/deployment/mcp_server/server.py"]
    }
  }
}
```

---

## 🛠️ Available Tools

- `parse_document(file_path: str, include_layout: bool = False)`: Submits any image (PNG, JPG, WebP) or PDF file to the AKS OCR pipeline, asynchronously polls for the result, and returns structured Markdown with optional layout bounding boxes.
