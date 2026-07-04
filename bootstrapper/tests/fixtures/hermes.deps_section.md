## 5. Dependencies & Integrations

> Auto-generated section — the **Current** subsections are derived from `services/hermes/service.yml`'s `data_flow.calls` field (and inverse passes). Re-run `python -m bootstrapper.docs.regen hermes` after manifest changes.

### 5.1 Current — Upstream (this service calls)

| Service | Category |
|---|---|
| litellm ↔ | llm |
| comfyui | media |
| searxng | media |
| stt-provider | media |
| tts-provider | media |
| airflow | agents |
| lightrag | agents |

### 5.2 Current — Downstream (services that call this)

| Service | Category |
|---|---|
| kong | infra |
| litellm ↔ | llm |
| n8n | agents |
| jupyterhub | apps |

### 5.3 Architecture diagram

![hermes architecture](./architecture.svg)

[Open the interactive HTML diagram](./architecture.html) for a full-screen view.

### 5.4 Future — Missing pair integrations

_No high-confidence opportunities identified._

### 5.5 Future — Candidate new services

_No high-confidence opportunities identified._

### 5.6 Future — Unused features in this service

_No high-confidence opportunities identified._
