# 6.6. Network And Routing Topology

Host ports, Kong aliases, direct service ports, backend-network-only services, and localhost-mode boundaries.

## 1. Diagram

[Open the interactive diagram](./network-routing-topology.html).

## 2. Notes

Localhost-mode services don't get a duplicate container on the backend network; the configured host gateway address is what lets in-network callers reach the host-run process instead.

## 3. Source Files

- `services/*/service.yml`
- `bootstrapper/tracks.yml`
- `bootstrapper/services/topology.py`
- `docs/deployment/source-configuration.md`
