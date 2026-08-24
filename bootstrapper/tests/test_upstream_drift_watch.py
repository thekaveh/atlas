from datetime import datetime, timezone
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import urllib.error

import pytest
import yaml

from scripts import upstream_drift_watch as watch


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "upstream-drift-watch.yml"


def _load_github_workflow(path: Path) -> dict:
    """Load a GitHub Actions workflow without YAML 1.1 treating ``on`` as bool."""

    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    if True in workflow:
        workflow["on"] = workflow.pop(True)
    return workflow


class _HttpResponse:
    """Complete stand-in for the small HTTP response surface the watcher uses."""

    def __init__(self, payload: bytes, status: int = 200):
        self._payload = payload
        self.status = status

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_load_curated_models_deduplicates_multimodal_entries(tmp_path):
    path = tmp_path / "models.yaml"
    path.write_text("content:\n  - name: qwen:latest\nvision:\n  - name: qwen:latest\n")
    assert watch.load_curated_ollama_models(path) == ("qwen:latest",)


def test_report_contains_stable_marker_and_all_failures():
    report = watch.render_report(
        [
            watch.ProbeResult("library", False, "too few"),
            watch.ProbeResult("images", False, "missing ref"),
        ],
        datetime(2026, 8, 24, tzinfo=timezone.utc),
    )
    assert "<!-- atlas-upstream-drift-watch -->" in report
    assert "too few" in report and "missing ref" in report


def test_load_manifest_image_refs_reads_literal_defaults_sorted_and_unique(tmp_path):
    services = tmp_path / "services"
    (services / "zeta").mkdir(parents=True)
    (services / "alpha").mkdir()
    (services / "zeta" / "service.yml").write_text(
        "images:\n  - var: Z_IMAGE\n    default: zeta:1\n"
    )
    (services / "alpha" / "service.yml").write_text(
        "images:\n  - var: A_IMAGE\n    default: zeta:1\n  - var: B_IMAGE\n    default: alpha:2\n"
    )
    assert watch.load_manifest_image_refs(services) == ("alpha:2", "zeta:1")


def test_report_normalizes_timestamp_and_bounds_detail():
    report = watch.render_report(
        [watch.ProbeResult("probe", False, "x" * 600)],
        datetime(2026, 8, 24, 12, 30),
    )
    assert "2026-08-24T12:30:00+00:00" in report
    assert len(report) < 700


@pytest.mark.parametrize(
    "contents, expected",
    [
        ("content: {name: qwen:latest}\n", "content"),
        ("content:\n  - qwen:latest\n", r"content\[0\]"),
        ("content:\n  - description: missing-name\n", r"content\[0\]\.name"),
    ],
)
def test_load_curated_models_rejects_malformed_declared_rows(tmp_path, contents, expected):
    path = tmp_path / "models.yaml"
    path.write_text(contents)
    with pytest.raises(ValueError, match=expected):
        watch.load_curated_ollama_models(path)


@pytest.mark.parametrize(
    "image_yaml, expected",
    [
        ("images:\n  - bad-row\n", r"images\[0\]"),
        ("images:\n  - var: IMAGE\n    default: 42\n", r"images\[0\]\.default"),
    ],
)
def test_load_manifest_image_refs_rejects_malformed_declared_rows(tmp_path, image_yaml, expected):
    services = tmp_path / "services"
    service = services / "demo"
    service.mkdir(parents=True)
    (service / "service.yml").write_text(image_yaml)
    with pytest.raises(ValueError, match=expected):
        watch.load_manifest_image_refs(services)


def test_load_manifest_image_refs_rejects_interpolated_defaults(tmp_path):
    services = tmp_path / "services"
    service = services / "demo"
    service.mkdir(parents=True)
    (service / "service.yml").write_text(
        "images:\n  - var: IMAGE\n    default: '${IMAGE_REF}'\n"
    )
    with pytest.raises(ValueError, match="literal"):
        watch.load_manifest_image_refs(services)


def test_discovery_allows_absent_optional_sections(tmp_path):
    models = tmp_path / "models.yaml"
    models.write_text("content:\n  - name: qwen:latest\n")
    services = tmp_path / "services"
    (services / "empty").mkdir(parents=True)
    (services / "empty" / "service.yml").write_text("name: empty\n")
    assert watch.load_curated_ollama_models(models) == ("qwen:latest",)
    assert watch.load_manifest_image_refs(services) == ()


def test_probe_ollama_library_requires_the_plausible_catalog_threshold(monkeypatch):
    monkeypatch.setattr(
        watch.ollama_library,
        "list_library_entries",
        lambda: [object()] * watch.ollama_library.MIN_PLAUSIBLE_ENTRIES,
    )
    assert watch.probe_ollama_library().ok is True

    monkeypatch.setattr(watch.ollama_library, "list_library_entries", lambda: [])
    result = watch.probe_ollama_library()
    assert result.ok is False
    assert "0" in result.detail


def test_probe_ollama_tags_uses_bounded_atlas_request_and_validates_schema(monkeypatch):
    captured = {}

    def _open(request, *, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return _HttpResponse(b'{"models": [{"name": "qwen:latest"}]}')

    monkeypatch.setattr(watch.urllib.request, "urlopen", _open)
    result = watch.probe_ollama_tags("http://127.0.0.1:11434/api/tags", timeout=2.5)

    assert result.ok is True
    assert captured["timeout"] == 2.5
    assert "Atlas" in captured["request"].get_header("User-agent")


@pytest.mark.parametrize(
    "response, expected",
    [
        (_HttpResponse(b"not-json"), "invalid JSON"),
        (_HttpResponse(b"[]"), "object"),
        (_HttpResponse(b'{"models": {}}'), "models"),
        (_HttpResponse(b'{"models": ["wrong"]}'), "models[0]"),
        (_HttpResponse(b'{"models": [{}]}'), "models[0]"),
        (_HttpResponse(b"{}", status=503), "HTTP 503"),
    ],
)
def test_probe_ollama_tags_rejects_malformed_or_non_success_response(monkeypatch, response, expected):
    monkeypatch.setattr(watch.urllib.request, "urlopen", lambda *_a, **_k: response)
    result = watch.probe_ollama_tags("http://ollama.invalid/api/tags", timeout=1.0)
    assert result.ok is False
    assert expected in result.detail


def test_probe_ollama_tags_converts_http_errors_to_failed_result(monkeypatch):
    error = urllib.error.HTTPError("http://ollama.invalid/api/tags", 502, "bad gateway", {}, None)
    monkeypatch.setattr(watch.urllib.request, "urlopen", lambda *_a, **_k: (_ for _ in ()).throw(error))
    result = watch.probe_ollama_tags("http://ollama.invalid/api/tags", timeout=1.0)
    assert result.ok is False
    assert "502" in result.detail


def test_probe_ollama_tags_accepts_model_alias_when_name_is_empty(monkeypatch):
    monkeypatch.setattr(
        watch.urllib.request,
        "urlopen",
        lambda *_a, **_k: _HttpResponse(b'{"models": [{"name": "", "model": "qwen:latest"}]}'),
    )
    assert watch.probe_ollama_tags("http://ollama.invalid/api/tags", timeout=1.0).ok


@pytest.mark.parametrize(
    "timeout",
    [0, -1, float("inf"), float("nan"), 61.0, 1e300, pytest.param(10**10000, id="huge-integer")],
)
@pytest.mark.parametrize(
    "probe, args",
    [
        (watch.probe_ollama_tags, ("http://ollama.invalid/api/tags",)),
        (watch.probe_curated_models, (("qwen:latest",),)),
    ],
)
def test_http_probes_reject_invalid_or_excessive_timeouts(monkeypatch, probe, args, timeout):
    monkeypatch.setattr(watch.urllib.request, "urlopen", lambda *_a, **_k: pytest.fail("request made"))
    result = probe(*args, timeout=timeout)
    assert result.ok is False
    assert "timeout" in result.detail


@pytest.mark.parametrize(
    "probe, args",
    [
        (watch.probe_ollama_tags, ("http://ollama.invalid/api/tags",)),
        (watch.probe_curated_models, (("qwen:latest",),)),
    ],
)
@pytest.mark.parametrize("error", [http.client.BadStatusLine("broken"), http.client.IncompleteRead(b"", 4)])
def test_http_probes_convert_protocol_failures_to_results(monkeypatch, probe, args, error):
    monkeypatch.setattr(
        watch.urllib.request,
        "urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(error),
    )
    assert probe(*args, timeout=1.0).ok is False


@pytest.mark.parametrize(
    "probe, args",
    [
        (watch.probe_ollama_tags, ("not a URL",)),
        (watch.probe_curated_models, (("qwen:latest",),)),
    ],
)
def test_http_probes_convert_malformed_urls_to_results(monkeypatch, probe, args):
    monkeypatch.setattr(
        watch.urllib.request,
        "Request",
        lambda *_a, **_k: (_ for _ in ()).throw(ValueError("malformed URL")),
    )
    result = probe(*args, timeout=1.0)
    assert result.ok is False
    assert "malformed URL" in result.detail


def test_probe_curated_models_reports_each_non_success_catalog_entry(monkeypatch):
    def _open(request, *, timeout):
        assert timeout == 1.5
        status = 404 if request.full_url.endswith("/missing") else 200
        return _HttpResponse(b"", status=status)

    monkeypatch.setattr(watch.urllib.request, "urlopen", _open)
    result = watch.probe_curated_models(("ok:latest", "missing:latest"), timeout=1.5)
    assert result.ok is False
    assert "missing:latest" in result.detail
    assert "ok:latest" not in result.detail


def _write_fake_docker(tmp_path: Path, body: str) -> None:
    docker = tmp_path / "docker"
    docker.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    docker.chmod(0o755)


def test_probe_manifest_images_uses_bounded_real_subprocess(tmp_path, monkeypatch):
    _write_fake_docker(tmp_path, "exit 0")
    monkeypatch.setenv("PATH", f"{tmp_path}:{__import__('os').environ['PATH']}")
    result = watch.probe_manifest_images(("example/image:1",), timeout=1.0, workers=1)
    assert result.ok is True


def test_probe_manifest_images_reports_nonzero_output_with_bounded_detail(tmp_path, monkeypatch):
    _write_fake_docker(tmp_path, "printf '%800s' '' | tr ' ' x >&2\nexit 9")
    monkeypatch.setenv("PATH", f"{tmp_path}:{__import__('os').environ['PATH']}")
    result = watch.probe_manifest_images(("broken/image:1",), timeout=1.0, workers=1)
    assert result.ok is False
    assert "broken/image:1" in result.detail
    assert len(result.detail) < 600
    assert "…" in result.detail


def test_probe_manifest_images_converts_subprocess_timeout_to_failure(tmp_path, monkeypatch):
    _write_fake_docker(tmp_path, "sleep 1")
    monkeypatch.setenv("PATH", f"{tmp_path}:{__import__('os').environ['PATH']}")
    result = watch.probe_manifest_images(("slow/image:1",), timeout=0.01, workers=1)
    assert result.ok is False
    assert "timed out" in result.detail


def test_probe_manifest_images_passes_explicit_bounded_subprocess_options(monkeypatch):
    captured = {}

    def _run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(watch.subprocess, "run", _run)
    assert watch.probe_manifest_images(("example/image:1",), timeout=3.0, workers=1).ok
    assert captured["command"] == ["docker", "buildx", "imagetools", "inspect", "example/image:1"]
    assert captured["kwargs"] == {
        "timeout": 3.0,
        "check": False,
        "capture_output": True,
        "text": True,
    }


@pytest.mark.parametrize(
    "timeout",
    [0, -1, float("inf"), float("nan"), 61.0, 1e300, pytest.param(10**10000, id="huge-integer")],
)
def test_probe_manifest_images_rejects_invalid_or_excessive_timeouts(monkeypatch, timeout):
    monkeypatch.setattr(watch.subprocess, "run", lambda *_a, **_k: pytest.fail("subprocess started"))
    result = watch.probe_manifest_images(("example/image:1",), timeout=timeout, workers=1)
    assert result.ok is False
    assert "timeout" in result.detail


@pytest.mark.parametrize("workers", [0, -1, 9])
def test_probe_manifest_images_rejects_worker_counts_outside_fixed_cap(monkeypatch, workers):
    monkeypatch.setattr(watch.subprocess, "run", lambda *_a, **_k: pytest.fail("subprocess started"))
    result = watch.probe_manifest_images(("example/image:1",), timeout=1.0, workers=workers)
    assert result.ok is False
    assert "workers" in result.detail


@pytest.mark.parametrize(
    "option, value",
    [
        ("--http-timeout", "0"),
        ("--http-timeout", "nan"),
        ("--http-timeout", "61"),
        ("--http-timeout", "1e10000"),
        ("--image-timeout", "-1"),
        ("--image-timeout", "inf"),
        ("--image-timeout", "61"),
        ("--image-timeout", "1e10000"),
        ("--image-workers", "0"),
        ("--image-workers", "9"),
    ],
)
def test_main_rejects_unbounded_timeout_and_worker_values(monkeypatch, option, value, tmp_path):
    monkeypatch.setattr(watch, "run_watch", lambda **_kwargs: pytest.fail("watch ran"))
    with pytest.raises(SystemExit, match="2"):
        watch.main([option, value, "--report-file", str(tmp_path / "report.md")])


def test_main_reports_discovery_failures_and_runs_independent_probes(monkeypatch, tmp_path):
    models = tmp_path / "models.yaml"
    models.write_text("content: [\n", encoding="utf-8")
    services = tmp_path / "services"
    service = services / "demo"
    service.mkdir(parents=True)
    (service / "service.yml").write_text("images: [\n", encoding="utf-8")
    report_path = tmp_path / "report.md"
    calls = []
    monkeypatch.setattr(
        watch,
        "probe_ollama_library",
        lambda: calls.append("library") or watch.ProbeResult("library", True, "ok"),
    )
    monkeypatch.setattr(
        watch,
        "probe_ollama_tags",
        lambda *_a, **_k: calls.append("tags") or watch.ProbeResult("tags", True, "ok"),
    )
    monkeypatch.setattr(watch, "probe_curated_models", lambda *_a, **_k: pytest.fail("curated probe ran"))
    monkeypatch.setattr(watch, "probe_manifest_images", lambda *_a, **_k: pytest.fail("image probe ran"))

    exit_code = watch.main([
        "--ollama-models", str(models),
        "--services-dir", str(services),
        "--report-file", str(report_path),
    ])
    report = report_path.read_text(encoding="utf-8")
    assert exit_code == 1
    assert calls == ["library", "tags"]
    assert "curated Ollama models" in report
    assert "manifest images" in report
    assert "could not read YAML source" in report


def test_run_watch_aggregates_results_in_probe_order(monkeypatch, tmp_path):
    monkeypatch.setattr(watch, "load_curated_ollama_models", lambda _path: ("model:latest",))
    monkeypatch.setattr(watch, "load_manifest_image_refs", lambda _path: ("image:1",))
    monkeypatch.setattr(watch, "probe_ollama_library", lambda: watch.ProbeResult("library", False, "small"))
    monkeypatch.setattr(watch, "probe_ollama_tags", lambda *_a, **_k: watch.ProbeResult("tags", True, "valid"))
    monkeypatch.setattr(watch, "probe_curated_models", lambda *_a, **_k: watch.ProbeResult("models", False, "missing"))
    monkeypatch.setattr(watch, "probe_manifest_images", lambda *_a, **_k: watch.ProbeResult("images", True, "resolved"))

    results = watch.run_watch(
        ollama_tags_url="http://ollama.invalid/api/tags",
        services_dir=tmp_path / "services",
        ollama_models=tmp_path / "models.yaml",
        http_timeout=1.0,
        image_timeout=1.0,
        image_workers=1,
    )
    assert [(result.name, result.ok) for result in results] == [
        ("library", False), ("tags", True), ("models", False), ("images", True)
    ]


@pytest.mark.parametrize("ok, expected_exit", [(True, 0), (False, 1)])
def test_main_writes_report_and_returns_aggregate_status(monkeypatch, tmp_path, ok, expected_exit):
    report_path = tmp_path / "report.md"
    monkeypatch.setattr(
        watch,
        "run_watch",
        lambda **_kwargs: (watch.ProbeResult("library", ok, "detail"),),
    )
    exit_code = watch.main([
        "--ollama-tags-url", "http://ollama.invalid/api/tags",
        "--services-dir", str(tmp_path / "services"),
        "--ollama-models", str(tmp_path / "models.yaml"),
        "--report-file", str(report_path),
        "--http-timeout", "2.0",
        "--image-timeout", "3.0",
        "--image-workers", "2",
    ])
    assert exit_code == expected_exit
    assert "<!-- atlas-upstream-drift-watch -->" in report_path.read_text(encoding="utf-8")


def test_upstream_drift_workflow_is_bounded_and_reconciles_one_marker_issue():
    workflow = _load_github_workflow(WORKFLOW_PATH)

    assert workflow["on"]["schedule"]
    assert workflow["on"]["workflow_dispatch"] is None
    assert workflow["permissions"] == {"contents": "read", "issues": "write"}
    assert workflow["concurrency"]["cancel-in-progress"] is False
    assert workflow["concurrency"]["group"] == "upstream-drift-watch"
    assert workflow["on"]["schedule"][0]["cron"].split()[0] != "0"

    job = workflow["jobs"]["watch"]
    assert job["timeout-minutes"] == 30
    steps = job["steps"]
    assert "<!-- atlas-upstream-drift-watch -->" in steps[0]["run"]
    assert "uses" not in steps[0]
    actions = [step["uses"] for step in steps if "uses" in step]
    assert actions == [
        "actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0",
        "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1",
        "astral-sh/setup-uv@11f9893b081a58869d3b5fccaea48c9e9e46f990",
    ]

    steps_by_id = {step["id"]: step for step in steps if "id" in step}
    image = steps_by_id["ollama_image"]["run"]
    assert "services/ollama/service.yml" in image
    assert "LLM_PROVIDER_IMAGE" in image
    assert "GITHUB_OUTPUT" in image

    start = steps_by_id["start_ollama"]["run"]
    assert "127.0.0.1:11434:11434" in start
    assert "${{" not in start
    assert steps_by_id["start_ollama"]["env"] == {
        "OLLAMA_IMAGE": "${{ steps.ollama_image.outputs.image }}"
    }
    assert "image_pattern=" in start
    assert "OLLAMA_IMAGE" in start

    readiness = steps_by_id["ollama_readiness"]["run"]
    assert "seq 1 10" in readiness
    assert "--max-time 1" in readiness
    assert "sleep 1" in readiness
    assert "within 20 seconds" in readiness
    assert "/api/tags" in readiness

    watch_step = steps_by_id["watch"]
    assert watch_step["continue-on-error"] is True
    assert "uv sync --project bootstrapper --group dev --locked" in "\n".join(
        step.get("run", "") for step in steps
    )
    assert "python -m scripts.upstream_drift_watch" in watch_step["run"]
    assert "--report-file" in watch_step["run"]
    assert "outcome=" in watch_step["run"]

    cleanup = steps_by_id["cleanup_ollama"]
    assert cleanup["if"] == "${{ always() }}"
    assert "docker rm -f atlas-upstream-drift-ollama" in cleanup["run"]

    reconcile = steps_by_id["reconcile_issue"]
    assert reconcile["if"] == "${{ always() }}"
    reconciliation = reconcile["run"]
    assert "<!-- atlas-upstream-drift-watch -->" in reconciliation
    assert 'title="Atlas upstream drift watch"' in reconciliation
    assert "gh api --paginate" in reconciliation
    assert "pull_request" in reconciliation
    assert "--limit 1000" not in reconciliation
    assert "uv " not in reconciliation
    assert "docker " not in reconciliation
    assert reconcile["env"] == {
        "GH_TOKEN": "${{ github.token }}",
        "GH_REPO": "${{ github.repository }}",
        "WATCH_OUTCOME": "${{ steps.watch.outputs.outcome }}",
        "REPORT_FILE": "${{ runner.temp }}/upstream-drift-report.md",
    }
    for command in (
        "gh issue create",
        "gh issue edit",
        "gh issue reopen",
        "gh issue comment",
        "gh issue close",
    ):
        assert command in reconciliation

    run_scripts = "\n".join(step.get("run", "") for step in steps).lower()
    assert "ollama pull" not in run_scripts


def _reconciliation_filter(script: str) -> str:
    match = re.search(r"^\s*issue_filter='(?P<filter>.*?)'\s*$", script, re.MULTILINE | re.DOTALL)
    assert match, "reconciliation must define its jq filter as issue_filter"
    return match.group("filter")


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is not installed")
def test_reconciliation_jq_filter_compiles_and_selects_the_lowest_exact_issue():
    script = _load_github_workflow(WORKFLOW_PATH)["jobs"]["watch"]["steps"][-1]["run"]
    result = subprocess.run(
        [
            "jq",
            "-s",
            "-r",
            "--arg",
            "marker",
            "<!-- atlas-upstream-drift-watch -->",
            "--arg",
            "title",
            "Atlas upstream drift watch",
            _reconciliation_filter(script),
        ],
        input=(
            '[{"number": 43, "state": "OPEN", "title": "Atlas upstream drift watch", '
            '"body": "<!-- atlas-upstream-drift-watch -->"}]\n'
            '[{"number": 7, "state": "CLOSED", "title": "Atlas upstream drift watch", '
            '"body": "<!-- atlas-upstream-drift-watch -->"}, '
            '{"number": 2, "state": "OPEN", "title": "Atlas upstream drift watch", '
            '"body": "<!-- atlas-upstream-drift-watch -->", "pull_request": {}}]\n'
        ),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "7\tCLOSED\n"


def _issue(number: int, state: str) -> dict[str, object]:
    return {
        "number": number,
        "state": state,
        "title": "Atlas upstream drift watch",
        "body": "<!-- atlas-upstream-drift-watch -->\nreport",
    }


def _run_reconciliation(tmp_path: Path, pages: list[list[dict[str, object]]], outcome: str):
    workflow = _load_github_workflow(WORKFLOW_PATH)
    script = workflow["jobs"]["watch"]["steps"][-1]["run"]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    gh = fake_bin / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "api" ]; then cat "$UPSTREAM_DRIFT_ISSUES"; exit 0; fi\n'
        'printf "%s\\n" "$*" >> "$UPSTREAM_DRIFT_GH_CALLS"\n',
        encoding="utf-8",
    )
    gh.chmod(0o755)
    issue_input = tmp_path / "issues.json"
    issue_input.write_text("\n".join(json.dumps(page) for page in pages), encoding="utf-8")
    report = tmp_path / "report.md"
    report.write_text("<!-- atlas-upstream-drift-watch -->\nreport\n", encoding="utf-8")
    calls = tmp_path / "gh-calls"
    environment = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "GH_REPO": "thekaveh/atlas",
        "WATCH_OUTCOME": outcome,
        "REPORT_FILE": str(report),
        "UPSTREAM_DRIFT_ISSUES": str(issue_input),
        "UPSTREAM_DRIFT_GH_CALLS": str(calls),
    }
    result = subprocess.run(
        ["bash", "-c", script],
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )
    recorded = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return result, recorded


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is not installed")
@pytest.mark.parametrize(
    "pages, outcome, expected_status, expected_calls",
    [
        ([[]], "1", 1, ["issue create --title Atlas upstream drift watch"]),
        ([[ _issue(43, "OPEN") ]], "1", 1, ["issue edit 43 --body-file"]),
        ([[ _issue(7, "CLOSED") ]], "1", 1, ["issue reopen 7", "issue edit 7 --body-file"]),
        ([[ _issue(43, "OPEN") ]], "0", 0, ["issue comment 43", "issue close 43 --reason completed"]),
        ([[ _issue(7, "CLOSED") ]], "0", 0, []),
    ],
)
def test_reconciliation_lifecycle_executes_the_matching_transition(
    tmp_path, pages, outcome, expected_status, expected_calls
):
    result, calls = _run_reconciliation(tmp_path, pages, outcome)
    assert result.returncode == expected_status, result.stderr
    assert len(calls) == len(expected_calls)
    for call, expected in zip(calls, expected_calls):
        assert call.startswith(expected)
