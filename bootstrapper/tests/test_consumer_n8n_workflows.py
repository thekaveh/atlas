"""Consumer n8n workflow seeding contract (#412).

A consumer declares n8n workflows in a versioned ``n8n_workflows`` block. Atlas
validates each (JSON parseable, credential-safe, optional checksum, stable/unique
id, non-colliding webhook routes), normalizes the workflow JSON to the stable
declared id (the idempotency key) with the activation policy baked in, compiles a
seed plan, and generates a compose overlay running an Atlas-owned ``n8n-seed``
container. All artifacts regenerate every start, so removing a manifest drops
only that consumer's workflows.
"""
from __future__ import annotations

import hashlib
import json
import textwrap
from pathlib import Path

import pytest

from core.consumer_manifest import (
    ConsumerManifestError,
    compile_n8n_plan,
    load_consumer_config,
    render_n8n_seed_overlay,
)


def _write_root(root: Path) -> None:
    (root / ".env.example").write_text("PROJECT_NAME=atlas\n", encoding="utf-8")


def _workflow_json(name: str = "WF", active: bool = False, nodes=None) -> str:
    return json.dumps(
        {"name": name, "active": active, "nodes": nodes or [], "connections": {}}
    )


def _write_consumer(root: Path, name: str, body: str, files: dict[str, str]) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    for fn, content in files.items():
        (d / fn).write_text(content, encoding="utf-8")
    manifest = d / "atlas.consumer.yml"
    manifest.write_text(f"name: {name}\n" + textwrap.dedent(body), encoding="utf-8")
    return manifest


# ── happy path ──────────────────────────────────────────────────────

def test_single_workflow_parsed_and_normalized(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_consumer(
        tmp_path, "rag-showcase",
        """
        n8n_workflows:
          version: 1
          workflows:
            - id: adaptive-rag
              path: ./adaptive.json
              active: "true"
              required_webhooks:
                - path: /webhook/adaptive-rag
                  method: GET
                  expect_status: 200
                  probe: true
        """,
        {"adaptive.json": _workflow_json("Adaptive RAG", active=False)},
    )
    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    assert len(config.n8n_workflows) == 1
    wf = config.n8n_workflows[0]
    assert wf.id == "adaptive-rag"
    assert wf.seed_id == "atlas-consumer-adaptive-rag"
    assert wf.consumer == "rag-showcase"
    assert wf.active == "true"
    assert wf.container_path == "/consumer-workflows/adaptive-rag.json"
    assert len(wf.webhooks) == 1 and wf.webhooks[0].probe is True

    # Normalized JSON: id rewritten to the NAMESPACED seed id (can't collide
    # with a user/stack workflow), active baked True.
    names = {a.path.name: a for a in config.n8n_artifacts}
    assert "adaptive-rag.json" in names and "plan.json" in names
    norm = json.loads(names["adaptive-rag.json"].content)
    assert norm["id"] == "atlas-consumer-adaptive-rag"
    assert norm["active"] is True


def test_active_fromjson_leaves_file_active_untouched(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_consumer(
        tmp_path, "c",
        """
        n8n_workflows:
          version: 1
          workflows:
            - id: wf
              path: ./wf.json
              active: fromJson
        """,
        {"wf.json": _workflow_json(active=True)},
    )
    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    norm = json.loads(
        {a.path.name: a for a in config.n8n_artifacts}["wf.json"].content
    )
    assert norm["active"] is True  # from the file
    assert config.n8n_workflows[0].active == "fromJson"


def test_plan_and_overlay_generated(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_consumer(
        tmp_path, "acme",
        """
        n8n_workflows:
          version: 1
          workflows:
            - id: acme-flow
              path: ./flow.json
        """,
        {"flow.json": _workflow_json()},
    )
    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    plan = json.loads(
        {a.path.name: a for a in config.n8n_artifacts}["plan.json"].content
    )
    assert plan["version"] == 1
    assert plan["namespace"] == "atlas-consumer-"
    assert plan["workflows"][0]["id"] == "acme-flow"  # declared id (logs/identity)
    assert plan["workflows"][0]["seed_id"] == "atlas-consumer-acme-flow"  # DB key
    assert plan["workflows"][0]["file"] == "/consumer-workflows/acme-flow.json"
    assert plan["workflows"][0]["consumer"] == "acme"

    assert config.n8n_overlay is not None
    assert config.n8n_overlay.path == tmp_path / "volumes/n8n/consumer-workflows.compose.yml"
    assert "n8n-seed" in config.n8n_overlay.content
    assert "${N8N_IMAGE}" in config.n8n_overlay.content


# ── multiple consumers / ordering / byte-stability ──────────────────

def test_multiple_consumers_isolated(tmp_path: Path) -> None:
    _write_root(tmp_path)
    a = _write_consumer(
        tmp_path, "alpha",
        "n8n_workflows:\n  version: 1\n  workflows:\n    - id: a-flow\n      path: ./a.json\n",
        {"a.json": _workflow_json()},
    )
    b = _write_consumer(
        tmp_path, "beta",
        "n8n_workflows:\n  version: 1\n  workflows:\n    - id: b-flow\n      path: ./b.json\n",
        {"b.json": _workflow_json()},
    )
    config = load_consumer_config(tmp_path, explicit_paths=[str(a), str(b)])
    ids = [wf.id for wf in config.n8n_workflows]
    assert ids == ["a-flow", "b-flow"]
    owners = {wf.id: wf.consumer for wf in config.n8n_workflows}
    assert owners == {"a-flow": "alpha", "b-flow": "beta"}


def test_generated_output_is_byte_stable(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_consumer(
        tmp_path, "stable",
        """
        n8n_workflows:
          version: 1
          workflows:
            - id: flow-a
              path: ./a.json
            - id: flow-b
              path: ./b.json
        """,
        {"a.json": _workflow_json("A"), "b.json": _workflow_json("B")},
    )
    first = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    second = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    assert compile_n8n_plan(first.n8n_workflows) == compile_n8n_plan(second.n8n_workflows)
    assert render_n8n_seed_overlay(first.n8n_workflows) == render_n8n_seed_overlay(
        second.n8n_workflows
    )
    a1 = {a.path.name: a.content for a in first.n8n_artifacts}
    a2 = {a.path.name: a.content for a in second.n8n_artifacts}
    assert a1 == a2


def test_no_n8n_workflows_yields_no_artifacts(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_consumer(
        tmp_path, "plain",
        "env:\n  values:\n    SOME_VAR: \"1\"\n",
        {},
    )
    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    assert config.n8n_workflows == ()
    assert config.n8n_artifacts == ()
    assert config.n8n_overlay is None


# ── collisions / ownership ──────────────────────────────────────────

def test_duplicate_id_within_consumer_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_consumer(
        tmp_path, "dup",
        """
        n8n_workflows:
          version: 1
          workflows:
            - id: same
              path: ./a.json
            - id: same
              path: ./b.json
        """,
        {"a.json": _workflow_json(), "b.json": _workflow_json()},
    )
    with pytest.raises(ConsumerManifestError, match="duplicate n8n_workflows id 'same'"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_duplicate_id_across_consumers_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    a = _write_consumer(
        tmp_path, "alpha",
        "n8n_workflows:\n  version: 1\n  workflows:\n    - id: shared\n      path: ./a.json\n",
        {"a.json": _workflow_json()},
    )
    b = _write_consumer(
        tmp_path, "beta",
        "n8n_workflows:\n  version: 1\n  workflows:\n    - id: shared\n      path: ./b.json\n",
        {"b.json": _workflow_json()},
    )
    with pytest.raises(ConsumerManifestError, match="declared by multiple consumers"):
        load_consumer_config(tmp_path, explicit_paths=[str(a), str(b)])


def test_webhook_route_collision_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_consumer(
        tmp_path, "routes",
        """
        n8n_workflows:
          version: 1
          workflows:
            - id: flow-a
              path: ./a.json
              required_webhooks:
                - path: /webhook/shared
                  method: POST
            - id: flow-b
              path: ./b.json
              required_webhooks:
                - path: /webhook/shared
                  method: POST
        """,
        {"a.json": _workflow_json(), "b.json": _workflow_json()},
    )
    with pytest.raises(ConsumerManifestError, match="webhook route .* declared by two"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_spoofed_owner_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_consumer(
        tmp_path, "honest",
        """
        n8n_workflows:
          version: 1
          workflows:
            - id: wf
              path: ./wf.json
              owner: someone-else
        """,
        {"wf.json": _workflow_json()},
    )
    with pytest.raises(ConsumerManifestError, match="cannot be spoofed"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


# ── file / json / credential validation ─────────────────────────────

def test_missing_workflow_file_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_consumer(
        tmp_path, "missing",
        "n8n_workflows:\n  version: 1\n  workflows:\n    - id: wf\n      path: ./nope.json\n",
        {},
    )
    with pytest.raises(ConsumerManifestError, match="does not exist"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_malformed_json_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_consumer(
        tmp_path, "badjson",
        "n8n_workflows:\n  version: 1\n  workflows:\n    - id: wf\n      path: ./wf.json\n",
        {"wf.json": "{not valid json"},
    )
    with pytest.raises(ConsumerManifestError, match="not valid JSON"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_embedded_credential_material_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    leaky = _workflow_json(
        nodes=[{"credentials": {"httpHeaderAuth": {"id": "1", "name": "c", "data": {"apiKey": "SECRET"}}}}]
    )
    manifest = _write_consumer(
        tmp_path, "leaky",
        "n8n_workflows:\n  version: 1\n  workflows:\n    - id: wf\n      path: ./wf.json\n",
        {"wf.json": leaky},
    )
    with pytest.raises(ConsumerManifestError, match="embeds credential material"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_credential_reference_by_id_name_is_allowed(tmp_path: Path) -> None:
    _write_root(tmp_path)
    ok = _workflow_json(
        nodes=[{"credentials": {"httpHeaderAuth": {"id": "1", "name": "cred"}}}]
    )
    manifest = _write_consumer(
        tmp_path, "ok",
        "n8n_workflows:\n  version: 1\n  workflows:\n    - id: wf\n      path: ./wf.json\n",
        {"wf.json": ok},
    )
    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    assert len(config.n8n_workflows) == 1


def test_secret_value_never_reaches_plan_or_overlay(tmp_path: Path) -> None:
    # Even a (rejected) leaky file's secret must not have leaked; a clean file's
    # generated plan/overlay carry only ids/paths, never node data.
    _write_root(tmp_path)
    manifest = _write_consumer(
        tmp_path, "clean",
        "n8n_workflows:\n  version: 1\n  workflows:\n    - id: wf\n      path: ./wf.json\n",
        {"wf.json": _workflow_json()},
    )
    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    plan = {a.path.name: a for a in config.n8n_artifacts}["plan.json"].content
    assert "connections" not in plan  # plan carries no workflow body
    assert "nodes" not in config.n8n_overlay.content


# ── checksum ────────────────────────────────────────────────────────

def test_checksum_match_accepted(tmp_path: Path) -> None:
    _write_root(tmp_path)
    body = _workflow_json("Checked")
    digest = hashlib.sha256(body.encode()).hexdigest()
    manifest = _write_consumer(
        tmp_path, "sum",
        f"""
        n8n_workflows:
          version: 1
          workflows:
            - id: wf
              path: ./wf.json
              checksum: "sha256:{digest}"
        """,
        {"wf.json": body},
    )
    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    assert config.n8n_workflows[0].checksum == f"sha256:{digest}"


def test_checksum_mismatch_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_consumer(
        tmp_path, "sum",
        """
        n8n_workflows:
          version: 1
          workflows:
            - id: wf
              path: ./wf.json
              checksum: "sha256:deadbeef"
        """,
        {"wf.json": _workflow_json()},
    )
    with pytest.raises(ConsumerManifestError, match="checksum mismatch"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


# ── schema / enums / webhooks ───────────────────────────────────────

@pytest.mark.parametrize("version", ["2", "0", "missing"])
def test_version_must_be_one(tmp_path: Path, version: str) -> None:
    _write_root(tmp_path)
    vline = "" if version == "missing" else f"  version: {version}\n"
    manifest = _write_consumer(
        tmp_path, "ver",
        "n8n_workflows:\n" + vline + "  workflows:\n    - id: wf\n      path: ./wf.json\n",
        {"wf.json": _workflow_json()},
    )
    with pytest.raises(ConsumerManifestError, match="n8n_workflows.version must be 1"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_bad_active_policy_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_consumer(
        tmp_path, "act",
        "n8n_workflows:\n  version: 1\n  workflows:\n    - id: wf\n      path: ./wf.json\n      active: maybe\n",
        {"wf.json": _workflow_json()},
    )
    with pytest.raises(ConsumerManifestError, match="active 'maybe' must be one of"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_bad_id_shape_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_consumer(
        tmp_path, "id",
        "n8n_workflows:\n  version: 1\n  workflows:\n    - id: Bad_Id\n      path: ./wf.json\n",
        {"wf.json": _workflow_json()},
    )
    with pytest.raises(ConsumerManifestError, match="must match"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_unknown_workflow_field_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_consumer(
        tmp_path, "typo",
        "n8n_workflows:\n  version: 1\n  workflows:\n    - id: wf\n      path: ./wf.json\n      activ: true\n",
        {"wf.json": _workflow_json()},
    )
    with pytest.raises(ConsumerManifestError, match="unknown field"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_bad_webhook_method_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_consumer(
        tmp_path, "wh",
        """
        n8n_workflows:
          version: 1
          workflows:
            - id: wf
              path: ./wf.json
              required_webhooks:
                - path: /webhook/x
                  method: DELETE
        """,
        {"wf.json": _workflow_json()},
    )
    with pytest.raises(ConsumerManifestError, match="webhook method"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_post_probe_defaults_off(tmp_path: Path) -> None:
    # A POST webhook without probe:true is tracked (collision) but never called.
    _write_root(tmp_path)
    manifest = _write_consumer(
        tmp_path, "wh",
        """
        n8n_workflows:
          version: 1
          workflows:
            - id: wf
              path: ./wf.json
              required_webhooks:
                - path: /webhook/x
                  method: POST
        """,
        {"wf.json": _workflow_json()},
    )
    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    probe = config.n8n_workflows[0].webhooks[0]
    assert probe.method == "POST" and probe.probe is False


def test_empty_workflows_list_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_consumer(
        tmp_path, "empty",
        "n8n_workflows:\n  version: 1\n  workflows: []\n",
        {},
    )
    with pytest.raises(ConsumerManifestError, match="non-empty list"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


# ── regression: adversarial-review findings ─────────────────────────

def test_seed_id_namespaced_so_it_cannot_hijack_a_user_workflow(tmp_path: Path) -> None:
    # Regression: a consumer id like "1" (a common n8n stack id) must NOT import
    # over an existing user/stack workflow. The DB id is the namespaced seed_id,
    # while the plan still carries the bare declared id for logs/reconcile.
    _write_root(tmp_path)
    manifest = _write_consumer(
        tmp_path, "sneaky",
        "n8n_workflows:\n  version: 1\n  workflows:\n    - id: shared-id\n      path: ./wf.json\n",
        {"wf.json": _workflow_json()},
    )
    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    wf = config.n8n_workflows[0]
    assert wf.id == "shared-id"
    assert wf.seed_id == "atlas-consumer-shared-id"
    norm = json.loads(
        {a.path.name: a for a in config.n8n_artifacts}["shared-id.json"].content
    )
    # The imported DB id is namespaced — never the bare declared id.
    assert norm["id"] == "atlas-consumer-shared-id"
    plan = json.loads(
        {a.path.name: a for a in config.n8n_artifacts}["plan.json"].content
    )
    assert plan["namespace"] == "atlas-consumer-"
    assert plan["workflows"][0]["seed_id"] == "atlas-consumer-shared-id"


def test_inline_string_credential_reference_rejected(tmp_path: Path) -> None:
    # Regression: the credential guard must reject a NON-mapping credential value
    # (a raw inline secret), not just a mapping carrying a ``data`` key.
    _write_root(tmp_path)
    leaky = _workflow_json(
        nodes=[{"credentials": {"httpHeaderAuth": "raw-secret-token"}}]
    )
    manifest = _write_consumer(
        tmp_path, "inline",
        "n8n_workflows:\n  version: 1\n  workflows:\n    - id: wf\n      path: ./wf.json\n",
        {"wf.json": leaky},
    )
    with pytest.raises(ConsumerManifestError, match="not an inline value"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_static_and_pin_data_stripped_from_normalized(tmp_path: Path) -> None:
    # Regression: staticData (runtime cursors/tokens) and pinData (pinned
    # execution payloads) are secret carriers and must not survive normalization.
    _write_root(tmp_path)
    body = json.dumps(
        {
            "name": "WF",
            "active": False,
            "nodes": [],
            "connections": {},
            "staticData": {"lastCursor": "SECRET-TOKEN"},
            "pinData": {"HTTP": [{"json": {"apiKey": "SECRET"}}]},
        }
    )
    manifest = _write_consumer(
        tmp_path, "runtime",
        "n8n_workflows:\n  version: 1\n  workflows:\n    - id: wf\n      path: ./wf.json\n",
        {"wf.json": body},
    )
    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    art = {a.path.name: a for a in config.n8n_artifacts}["wf.json"]
    norm = json.loads(art.content)
    assert "staticData" not in norm
    assert "pinData" not in norm
    assert "SECRET" not in art.content and "SECRET-TOKEN" not in art.content
