---
name: creative-comfyui-host-override
description: Override the bundled creative-comfyui skill's hardcoded ComfyUI host.
extends: creative-comfyui
priority: 100
---

# ComfyUI host override

The bundled `creative-comfyui` skill defaults to `http://127.0.0.1:8188`,
which doesn't exist inside this container. The atlas stack runs
ComfyUI as a sibling service on the Docker network — reachable at the
URL below.

## 1. Overrides

| Setting | Value |
|---------|-------|
| `comfyui.host` | `${COMFYUI_INTERNAL_URL}` |
| `comfyui.url`  | `${COMFYUI_INTERNAL_URL}` |
| `--host` flag  | `${COMFYUI_INTERNAL_URL}` |

Skills that shell out to `python3 scripts/run_workflow.py` should pass
`--host ${COMFYUI_INTERNAL_URL}` instead of relying on the default.

## 2. Verification

```bash
curl -fsS ${COMFYUI_INTERNAL_URL}/system_stats
```

If the call fails, ComfyUI is disabled in the stack (`COMFYUI_SOURCE=disabled`)
or unhealthy. Check `docker compose logs comfyui`.
