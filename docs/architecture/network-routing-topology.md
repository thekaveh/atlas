# 8.6. Network And Routing Topology

Host ports, Kong aliases, direct service ports, backend-network-only services, and localhost-mode boundaries.

## 1. Diagram

![Network And Routing Topology architecture diagram](../diagrams/img/architecture-network-routing-topology.png)

[Open the full-size diagram](./network-routing-topology.html).

## 2. Notes

The host-gateway address depends on the runtime; it is not a fixed IP. Docker resolves the literal `host-gateway` value in `extra_hosts` itself. Podman has no such shortcut: `resolve_host_gateway_ip()` in `bootstrapper/utils/system.py` queries the default bridge network's IPAM gateway, then falls back to a throwaway container. Localhost-source endpoints always name `host.docker.internal`, which each reading container maps to that gateway (#1361).

## 3. Source Files

- `bootstrapper/utils/kong_config_generator.py`
- `bootstrapper/utils/system.py`
- `bootstrapper/services/topology.py`
- `services/kong/service.yml`
