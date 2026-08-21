"""Port pre-flight must probe only ports something will actually bind.

`handle_port_configuration` turns any conflict into `return False`, and both
the linear and Textual pipelines abort on it — so a port probed for a service
that ships disabled is a launch blocker for no reason.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _manager(tmp_path: Path, env_body: str):
    from core.port_manager import PortManager

    (tmp_path / ".env").write_text(env_body, encoding="utf-8")
    manager = PortManager(str(REPO_ROOT))
    manager.config_parser.env_file_path = tmp_path / ".env"
    return manager


def test_ports_of_disabled_services_are_not_probed(tmp_path):
    """Roughly half the probed ports belong to services shipping disabled.

    Airflow, Grafana, Prometheus, Ray, Spark, Trino, Jenkins, Zeppelin,
    Redpanda, Langfuse and MLflow all default to `disabled`; nothing will ever
    bind their ports, yet an unrelated host process on one aborted `./start.sh`.
    """
    manager = _manager(tmp_path, "GRAFANA_SOURCE=disabled\nPROMETHEUS_SOURCE=disabled\n")
    assignments = manager.calculate_port_assignments(63000)
    grafana_port = assignments["GRAFANA_PORT"]

    # Drive the REAL entry point with the port occupied, rather than asserting
    # on the private helper — a test that only calls the helper stays green
    # even if the call site stops consulting it.
    occupied = {grafana_port}
    manager.check_port_availability = lambda port: port not in occupied

    assert manager.get_port_conflicts(63000) == {}, (
        "a disabled service's port aborted the launch"
    )
    assert grafana_port not in manager.check_port_range_availability(63000)


def test_an_enabled_service_is_still_probed(tmp_path):
    manager = _manager(tmp_path, "GRAFANA_SOURCE=container\n")
    grafana_port = manager.calculate_port_assignments(63000)["GRAFANA_PORT"]
    manager.check_port_availability = lambda port: port != grafana_port

    assert manager.get_port_conflicts(63000) == {"GRAFANA_PORT": grafana_port}
    assert grafana_port in manager.check_port_range_availability(63000)


def test_unreadable_sources_fail_open(tmp_path):
    """Skipping nothing is the safe direction — probe everything."""
    manager = _manager(tmp_path, "")
    grafana_port = manager.calculate_port_assignments(63000)["GRAFANA_PORT"]
    manager.check_port_availability = lambda port: port != grafana_port
    assert manager.get_port_conflicts(63000) == {"GRAFANA_PORT": grafana_port}


def test_the_assignment_pattern_does_not_run_past_a_blank_value():
    """`\\s*` around the `=` matches a NEWLINE.

    Under `re.MULTILINE` the `$` matches before a newline, but `\\s` matches the
    newline itself — so for a blank `VAR=` the value group ran into the
    following line and swallowed the whole next assignment.

    Scope, stated honestly: no current caller reaches this. `update_env_ports`
    leaves a blank value alone, and I could not reproduce end-to-end corruption
    through it. This pins the pattern's own contract so the trap cannot be
    sprung by a future caller.
    """
    import re

    from core.port_manager import _assignment_pattern

    text = "KONG_HTTP_PORT=\nKONG_HTTPS_PORT=63001\n"
    match = re.search(_assignment_pattern("KONG_HTTP_PORT"), text, re.MULTILINE)
    assert match is not None
    assert match.group(2) == "", "the value group ran into the next line"

    rewritten = re.sub(
        _assignment_pattern("KONG_HTTP_PORT"),
        lambda m: m.group(1) + "64000" + m.group(3),
        text,
        flags=re.MULTILINE,
    )
    assert rewritten == "KONG_HTTP_PORT=64000\nKONG_HTTPS_PORT=63001\n"


def test_the_assignment_pattern_still_handles_comments_and_padding():
    import re

    from core.port_manager import _assignment_pattern

    for line, value in [
        ("N8N_PORT=63075", "63075"),
        ("N8N_PORT=63075   ", "63075"),
        ("N8N_PORT=63075  # the n8n UI", "63075"),
        ("N8N_PORT = 63075", "63075"),
    ]:
        match = re.search(_assignment_pattern("N8N_PORT"), line, re.MULTILINE)
        assert match is not None, line
        assert match.group(2) == value, line
