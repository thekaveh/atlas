# 6.2. Atlas Platform Overview

User entrypoints, Kong, apps, agents, LLM core, data stores, and cloud-provider boundaries.

## 1. Diagram

[Open the interactive diagram](./platform-overview.html).

## 2. How To Read This View

Clients enter through Kong or a deliberately published direct port. Application and agent services consume the shared LLM and data layers; LiteLLM keeps local inference and cloud-provider credentials behind one OpenAI-compatible boundary.

## 3. Source Files

- `services/*/service.yml`
- `bootstrapper/tracks.yml`
- `services/topology.py`
- `docs/deployment/source-configuration.md`
