"""Regression test for the Kong + Ray slot-pin invariant.

The slot allocator orders services by topological rank, then breaks ties
alphabetically. Two ports are load-bearing across the stack:

- `KONG_HTTP_PORT = 63000` — every Kong route alias in the docs (`*.localhost:63000`)
  assumes this value. Documentation and operator muscle-memory both
  encode it.
- `RAY_DASHBOARD_PORT = 63002` — `BASE_PORT + 2` ships in the wizard's
  default landing summary and in linked docs.

Both hold today only because Kong and Ray sort early enough by the
combined topo-rank + alphabetical tie-break. The convention (spelled out
in services/grafana/service.yml's depends_on comment) is that every
new infra-band service must add `kong` and `ray` to its
`depends_on.required`, forcing them to sort BEFORE that service and
preserving the 63000 / 63002 invariant.

`bootstrapper/tests/test_slot_allocator.py` exercises the allocator
with small synthetic manifest sets. It does NOT load the real
`services/*/service.yml` tree, so it cannot catch a regression where
the invariant breaks because of how the real 24-service topology lays
out together. This test closes that gap by running the actual
production topology and asserting the two canonical ports.

If this test fails, the most likely cause is a new (or modified)
infra-band service missing `kong`/`ray` from its `depends_on.required`.
See the memory file linked above.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.topology import build_topology


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SERVICES_ROOT = REPO_ROOT / "services"


@pytest.fixture(scope="module")
def real_topology():
    return build_topology(SERVICES_ROOT)


def test_kong_http_port_is_63000(real_topology) -> None:
    assert real_topology.port_defaults["KONG_HTTP_PORT"] == 63000, (
        "KONG_HTTP_PORT drifted off 63000. Every `*.localhost:63000` "
        "route in the docs and every wizard message assumes this value. "
        "If a new infra-band service is missing `kong` from its "
        "`depends_on.required`, the alphabetical tie-break will displace "
        "Kong from slot 0. Add `kong` to that service's depends_on.required."
    )


def test_ray_dashboard_port_is_63002(real_topology) -> None:
    assert real_topology.port_defaults["RAY_DASHBOARD_PORT"] == 63002, (
        "RAY_DASHBOARD_PORT drifted off 63002. The wizard's default "
        "landing summary and linked docs both encode this value. If a "
        "new infra-band service is missing `ray` from its "
        "`depends_on.required`, the alphabetical tie-break will displace "
        "Ray from slot 2. Add `ray` to that service's depends_on.required."
    )
