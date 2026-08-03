# 6.2. Atlas Platform Overview

User entrypoints, Kong, apps, agents, LLM core, data stores, and cloud-provider boundaries.

## 1. Diagram

[Open the full-size diagram](./platform-overview.html).

## 2. Notes

Direct published ports bypass Kong deliberately, for host tools that can't use the `*.localhost` gateway. All model traffic — local and cloud — is funneled through LiteLLM so credentials and routing live in exactly one place (see [LLM provider flow](./llm-provider-flow.md)).

## 3. Source Files

- `services/*/service.yml`
- `bootstrapper/services/topology.py`
