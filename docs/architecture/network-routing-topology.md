# 6.6. Network And Routing Topology

Host ports, Kong aliases, direct service ports, backend-network-only services, and localhost-mode boundaries.

## 1. Diagram

[Open the interactive diagram](./network-routing-topology.html).

## 2. How To Read This View

Kong provides stable `*.localhost` entrypoints while published ports support direct host access. Internal-only traffic remains on the Compose backend network. Localhost modes cross the container boundary through the configured host gateway instead of starting a duplicate workload.

## 3. Source Files

- `services/*/service.yml`
- `bootstrapper/tracks.yml`
- `bootstrapper/services/topology.py`
- `docs/deployment/source-configuration.md`
