# Overview

Atlas is organized around SOURCE values, tracks, manifests, generated docs, and
Kong-fronted service URLs. The stack can run local containers, connect to host
services, disable optional services, or route cloud LLM providers through
LiteLLM without changing application code.

Primary source files:

- `services/*/service.yml`
- `bootstrapper/tracks.yml`
- `services/topology.py`
- `.env.example`
- `docs/deployment/source-configuration.md`
