# 6.6. Network And Routing Topology

Host ports, Kong aliases, direct service ports, backend-network-only services, and localhost-mode boundaries.

## 1. Diagram

[Open the interactive diagram](./network-routing-topology.html).

## 2. Notes

The host-gateway address is runtime-dependent, not a fixed IP: Docker accepts the literal `host-gateway` value in `extra_hosts` and resolves it internally, but Podman has no such shortcut — it queries the default bridge network's IPAM gateway IP directly (`resolve_host_gateway_ip()` in `bootstrapper/utils/system.py`), falling back to a throwaway container if that lookup fails.

## 3. Source Files

- `bootstrapper/utils/kong_config_generator.py`
- `bootstrapper/services/topology.py`
- `services/kong/service.yml`
