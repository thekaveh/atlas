"""#720: Atlas restarts n8n after seeding to register a consumer's production
webhook when the workflow is activated without an N8N_API_KEY.

The restart behavior was empirically verified on n8nio/n8n:2.28.2. Atlas keeps
the conservative restart on 2.36.7: the current image retains the
`publish:workflow --id` CLI contract, while an API-key activation still uses
the live public API and does not need the restart.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))


def test_effective_active_true_false_and_fromjson(tmp_path):
    import start

    assert start._n8n_workflow_effective_active(NS(active="true", source_path=tmp_path / "x")) is True
    assert start._n8n_workflow_effective_active(NS(active="false", source_path=tmp_path / "x")) is False
    f = tmp_path / "wf.json"
    f.write_text(json.dumps({"active": True}), encoding="utf-8")
    assert start._n8n_workflow_effective_active(NS(active="fromJson", source_path=f)) is True
    f.write_text(json.dumps({"active": False}), encoding="utf-8")
    assert start._n8n_workflow_effective_active(NS(active="fromJson", source_path=f)) is False
    # unreadable fromJson -> inactive (fail closed)
    assert start._n8n_workflow_effective_active(NS(active="fromJson", source_path=tmp_path / "missing")) is False


def test_needs_reactivation_restart_predicate(tmp_path):
    import start

    active = NS(active="true", source_path=tmp_path / "x")
    inactive = NS(active="false", source_path=tmp_path / "x")
    C = lambda wfs: NS(n8n_workflows=tuple(wfs))

    # active workflow + no key + n8n enabled -> restart needed
    assert start._n8n_needs_reactivation_restart({"N8N_SOURCE": "container"}, C([active])) is True
    # key present -> API path already registered -> no restart
    assert start._n8n_needs_reactivation_restart({"N8N_SOURCE": "container", "N8N_API_KEY": "k"}, C([active])) is False
    # n8n disabled / unset -> no restart
    assert start._n8n_needs_reactivation_restart({"N8N_SOURCE": "disabled"}, C([active])) is False
    assert start._n8n_needs_reactivation_restart({}, C([active])) is False
    # no active workflow -> no restart
    assert start._n8n_needs_reactivation_restart({"N8N_SOURCE": "container"}, C([inactive])) is False
    assert start._n8n_needs_reactivation_restart({"N8N_SOURCE": "container"}, C([])) is False


def test_reactivate_n8n_restarts_only_when_needed(tmp_path):
    import start

    active = NS(active="true", source_path=tmp_path / "x")

    class _CP:
        def __init__(self, env, wfs):
            self._env = env
            self._wfs = wfs

        def parse_env_file(self):
            return dict(self._env)

        def load_consumer_config(self):
            return NS(n8n_workflows=tuple(self._wfs))

    class _DM:
        def __init__(self):
            self.calls = []

        def execute_compose_command(self, args):
            self.calls.append(args)
            return 0

    class _Banner:
        def show_status_message(self, *a, **k):
            pass

    def make(env, wfs):
        s = start.AtlasStarter.__new__(start.AtlasStarter)
        s.config_parser = _CP(env, wfs)
        s.docker_manager = _DM()
        s.banner = _Banner()
        return s

    # needs restart -> restarts n8n
    s = make({"N8N_SOURCE": "container"}, [active])
    s._reactivate_n8n_if_needed()
    assert ["restart", "n8n"] in s.docker_manager.calls

    # key present -> no restart
    s = make({"N8N_SOURCE": "container", "N8N_API_KEY": "k"}, [active])
    s._reactivate_n8n_if_needed()
    assert s.docker_manager.calls == []


def test_seed_workflows_publishes_when_no_api_key():
    """The seed's no-key branch persists active=true via publish:workflow instead
    of the old passive 'registers on next restart' no-op."""
    seed = (REPO_ROOT / "services" / "n8n" / "init" / "scripts" / "seed-workflows.js").read_text(
        encoding="utf-8"
    )
    assert "publish:workflow" in seed
    assert "note: '${wf.id}' active but N8N_API_KEY unset" not in seed
