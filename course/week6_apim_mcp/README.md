# Week 6: 🛡️ Enterprise Gateways & Claude Code MCP

> 📘 **Article:** _link coming soon_

<p align="center"><img src="../../images/week6_apim_mcp.png" width="600" alt="Week 6"></p>

## 🎯 What You'll Learn

* Why the Producer only has an **internal load balancer IP**, and why a perimeter gateway sits in front of it.
* **Azure API Management** in Internal VNet mode: backends, operations, products, subscription keys.
* Governance policies: **JWT validation** and **rate limiting** as a guard against runaway GPU scale-out.
* Wrapping the pipeline as an **MCP server** so AI agents like Claude Code can call it as a tool.

## 🔍 Where It Lives

| File | What to look at |
| :--- | :--- |
| [`deploy/k8s/networking/service.yml`](../../deploy/k8s/networking/service.yml) | The `azure-load-balancer-internal` annotation |
| [`deploy/apim/apim-policy.xml`](../../deploy/apim/apim-policy.xml) | JWT + rate-limit policy |
| [`services/mcp_server/server.py`](../../services/mcp_server/server.py) | The `parse_document` tool |
| [`deploy/k8s/apps/mcp-server-deployment.yml`](../../deploy/k8s/apps/mcp-server-deployment.yml) | In-cluster MCP server (SSE) |

## 🛠️ Hands-On

```bash
# MCP server → Claude Code
make build-mcp deploy
make port-forward-mcp           # terminal 1
make mcp-register               # terminal 2
claude "Use tnm-ocr to parse samples/sample_image.png and summarize it"

# APIM perimeter (optional, 30–45 min to provision)
make apim                       # instance + API + policy + subscription key
```

The full manual walkthrough, including how to reach an internal-mode APIM, is in [`docs/deployment_guide.md`](../../docs/deployment_guide.md) (sections 5–6).

## ✅ Deliverables

- [ ] `kubectl get svc ocr-api-service` shows a **private** IP as its external address.
- [ ] Claude Code calls `parse_document` and returns a summary of the sample image.
- [ ] *(APIM track)* A request without a subscription key is rejected with `401`, and one with the key from `make apim-key` succeeds.
- [ ] *(APIM track)* Exceed the rate limit (100 calls / 60s per subscription) and capture the `429` response.
- [ ] Final write-up: a one-page architecture summary of the full system you deployed, covering the request path, the scaling signals and the security boundaries.

## 🎓 Wrapping Up

Run `make down` to delete everything once you've captured your deliverables.

---

**← Previous:** [Week 5](../week5_async_architecture/README.md) · **Back to the [course guide](../README.md)**
