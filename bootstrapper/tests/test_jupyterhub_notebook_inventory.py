from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
NOTEBOOK_DIR = ROOT / "services" / "jupyterhub" / "build" / "notebooks"
COMPOSE_FILE = ROOT / "services" / "jupyterhub" / "compose.yml"
REQUIREMENTS_FILE = ROOT / "services" / "jupyterhub" / "build" / "requirements.txt"
STARTUP_FILE = ROOT / "services" / "jupyterhub" / "build" / "scripts" / "startup.sh"

MODULE_DISTRIBUTIONS = {
    "boto3": "boto3",
    "ccxt": "ccxt",
    "fastmcp": "fastmcp",
    "chonkie": "chonkie",
    "dotenv": "python-dotenv",
    "httpx": "httpx",
    "langchain_openai": "langchain-openai",
    "mlflow": "mlflow",
    "neo4j": "neo4j",
    "openai": "openai",
    "openbb": "openbb",
    "pandas": "pandas",
    "pyspark": "pyspark-client",
    "ragas": "ragas",
    "ray": "ray",
    "redis": "redis",
    "sqlalchemy": "sqlalchemy",
    "supabase": "supabase",
    "weaviate": "weaviate-client",
}
LOCAL_MODULES = {"atlas_finance"}


def _markdown_mentions(path: Path) -> set[str]:
    return set(re.findall(r"`([0-9][0-9]_[^`]+\.ipynb)`", path.read_text(encoding="utf-8")))


def test_jupyterhub_notebook_inventory_matches_docs_and_starter_notebook():
    notebooks = {p.name for p in NOTEBOOK_DIR.glob("*.ipynb")}
    readme_mentions = _markdown_mentions(ROOT / "services" / "jupyterhub" / "README.md")
    startup_mentions = _markdown_mentions(STARTUP_FILE)

    starter = json.loads((NOTEBOOK_DIR / "00_environment_check.ipynb").read_text())
    starter_text = "\n".join(
        "".join(cell.get("source", []))
        for cell in starter.get("cells", [])
        if cell.get("cell_type") == "markdown"
    )
    starter_mentions = set(re.findall(r"`([0-9][0-9]_[^`]+\.ipynb)`", starter_text))
    starter_mentions.add("00_environment_check.ipynb")

    assert readme_mentions == notebooks
    assert starter_mentions == notebooks
    assert startup_mentions == notebooks


def test_environment_check_is_bounded_redacted_and_truthfully_scoped():
    notebook = json.loads((NOTEBOOK_DIR / "00_environment_check.ipynb").read_text())
    markdown = "\n".join(
        "".join(cell.get("source", []))
        for cell in notebook.get("cells", [])
        if cell.get("cell_type") == "markdown"
    )
    code = "\n".join(
        "".join(cell.get("source", []))
        for cell in notebook.get("cells", [])
        if cell.get("cell_type") == "code"
    )

    headings = [line for line in markdown.splitlines() if line.startswith("## ")]
    assert headings
    assert all(re.match(r"^## \d+\. ", heading) for heading in headings)
    assert "all Atlas services" not in markdown
    assert "configured (value hidden)" in code
    assert 'os.getenv("DATABASE_URL", "not set")' not in code
    assert 'os.getenv("REDIS_URL", "not set")' not in code
    assert "timeout=5.0" in code
    assert 'connect_args={"connect_timeout": 5}' in code
    assert "socket_connect_timeout=5" in code
    assert "connection_timeout=5" in code
    for variable in (
        "LITELLM_BASE_URL",
        "WEAVIATE_URL",
        "COMFYUI_BASE_URL",
        "N8N_BASE_URL",
        "SEARXNG_URL",
        "BACKEND_API_URL",
    ):
        assert variable in code


def test_jupyterhub_notebook_cell_ids_are_nbformat_45_complete_and_unique():
    for path in sorted(NOTEBOOK_DIR.glob("*.ipynb")):
        notebook = json.loads(path.read_text(encoding="utf-8"))
        assert notebook.get("nbformat") == 4
        assert notebook.get("nbformat_minor", 0) >= 5, (
            f"{path} must declare nbformat 4.5+ so cell IDs are part of "
            "the file contract"
        )
        cells = notebook.get("cells", [])
        ids = [cell.get("id") for cell in cells]
        assert all(isinstance(cell_id, str) and cell_id for cell_id in ids), (
            f"{path} has cells without IDs"
        )
        assert len(ids) == len(set(ids)), f"{path} has duplicate cell IDs"


def test_python_notebook_cells_compile_and_direct_imports_are_declared():
    declared = set()
    for line in REQUIREMENTS_FILE.read_text(encoding="utf-8").splitlines():
        requirement = line.split("#", 1)[0].strip()
        if not requirement or requirement.startswith("-"):
            continue
        name = re.split(r"[<>=!~\[]", requirement, maxsplit=1)[0]
        declared.add(name.lower().replace("_", "-"))

    for path in sorted(NOTEBOOK_DIR.glob("*.ipynb")):
        notebook = json.loads(path.read_text(encoding="utf-8"))
        if notebook.get("metadata", {}).get("kernelspec", {}).get("language") == "scala":
            continue
        imports = set()
        for index, cell in enumerate(notebook.get("cells", [])):
            if cell.get("cell_type") != "code":
                continue
            source = "".join(cell.get("source", []))
            tree = ast.parse(source, filename=f"{path.name}:cell-{index}")
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name.split(".", 1)[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module.split(".", 1)[0])

        third_party = imports - set(sys.stdlib_module_names) - LOCAL_MODULES
        unknown = third_party - MODULE_DISTRIBUTIONS.keys()
        assert not unknown, f"{path.name} has unmapped direct imports: {sorted(unknown)}"
        missing = {
            MODULE_DISTRIBUTIONS[module]
            for module in third_party
            if MODULE_DISTRIBUTIONS[module] not in declared
        }
        assert not missing, f"{path.name} imports undeclared packages: {sorted(missing)}"


def test_python_and_scala_spark_examples_share_runtime_contracts():
    def notebook_text(name: str, cell_type: str | None = None) -> str:
        notebook = json.loads((NOTEBOOK_DIR / name).read_text(encoding="utf-8"))
        return "\n".join(
            "".join(cell.get("source", []))
            for cell in notebook.get("cells", [])
            if cell_type is None or cell.get("cell_type") == cell_type
        )

    python_spark = notebook_text("09_spark_connect.ipynb")
    scala_spark = notebook_text("10_spark_scala.ipynb")
    scala_basics_code = notebook_text("08_scala_basics.ipynb", "code")

    for text in (python_spark, scala_spark):
        assert "SPARK_REMOTE" in text
        assert "MINIO_BUCKET_SPARK_HISTORY" in text  # bucket follows the env (#1392)
    assert "LITELLM_DEFAULT_MODEL" in scala_basics_code
    assert '"ollama/qwen3.8:latest"' not in scala_basics_code


def test_jupyterhub_allow_origin_flag_uses_env_knob():
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    command = compose["services"]["jupyterhub"]["command"]

    # Not "*": a page on another localhost port could open a cookie-authenticated
    # terminal websocket (2026-10-08 run, cycle 57).
    assert "--ServerApp.allow_origin=${JUPYTER_ALLOW_ORIGIN:-}" in command
def test_env_vars_the_scala_kernel_hard_requires_are_injected():
    """The Scala kernel reads sys.env only and has no dotenv fallback.

    Python notebooks can call load_dotenv() and recover a value the container
    was never given; the Scala kernel cannot. So any variable a Scala cell
    reads and then asserts non-empty has to arrive through compose, or the cell
    aborts on every run no matter how the operator configured .env.
    """
    import json
    import re

    compose = COMPOSE_FILE.read_text(encoding="utf-8")
    required: dict[str, str] = {}

    for notebook in sorted(NOTEBOOK_DIR.glob("*.ipynb")):
        doc = json.loads(notebook.read_text(encoding="utf-8"))
        for cell in doc.get("cells", []):
            source = "".join(cell.get("source", []))
            if "sys.env.getOrElse" not in source:
                continue
            for name in re.findall(
                r'sys\.env\.getOrElse\(\s*"([A-Z][A-Z0-9_]*)"\s*,\s*""\s*\)', source
            ):
                if re.search(rf'require\(\s*\w+\.nonEmpty', source):
                    required[name] = notebook.name

    assert required, (
        "no Scala cell hard-requires an env var any more -- re-derive this "
        "contract before deleting the test"
    )

    missing = {
        name: origin
        for name, origin in required.items()
        if not re.search(rf"^\s+{re.escape(name)}:", compose, re.MULTILINE)
    }
    assert not missing, (
        "these are required by a Scala notebook cell but never injected into "
        f"the jupyterhub container, so the cell always aborts: {missing}"
    )


def test_advanced_sql_notebook_follows_the_configured_iceberg_buckets():
    notebook = json.loads(
        (NOTEBOOK_DIR / "12_iceberg_advanced_sql.ipynb").read_text(encoding="utf-8")
    )
    code = "\n".join(
        "".join(cell["source"]) for cell in notebook["cells"]
        if cell["cell_type"] == "code"
    )
    environment = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))[
        "services"
    ]["jupyterhub"]["environment"]
    for var in ("MINIO_BUCKET_ICEBERG_LANDING", "MINIO_BUCKET_ICEBERG_CHECKPOINTS"):
        assert f'os.environ.get("{var}"' in code
        assert var in environment
    assert "s3a://landing/" not in code and "s3a://checkpoints/" not in code


# ─── bucket names follow MINIO_BUCKET_*; a disabled Weaviate is reported (#1392)


def test_notebook_code_names_no_minio_bucket_literally():
    """A renamed bucket got probe writes to one that does not exist."""
    literal = re.compile(r"s3a://(spark-history|landing|checkpoints)(?=[/\"'\s]|$)")
    code = [
        "".join(cell.get("source", []))
        for path in NOTEBOOK_DIR.glob("*.ipynb")
        for cell in json.loads(path.read_text(encoding="utf-8"))["cells"]
        if cell["cell_type"] == "code"
    ]
    zeppelin = ROOT / "services" / "zeppelin" / "notebooks"
    code += [
        paragraph.get("text", "")
        for path in zeppelin.glob("*.zpln")
        for paragraph in json.loads(path.read_text(encoding="utf-8")).get("paragraphs", [])
        if not paragraph.get("text", "").lstrip().startswith("%md")
    ]
    assert [text for text in code if literal.search(text)] == []


def test_the_rag_notebook_reports_a_disabled_weaviate_instead_of_failing(monkeypatch, capsys):
    import types

    cells = json.loads((NOTEBOOK_DIR / "02_langchain_rag.ipynb").read_text(encoding="utf-8"))["cells"]
    setup, store, query = ("".join(cells[i]["source"]) for i in (3, 7, 9))
    monkeypatch.setenv("OPENAI_API_BASE", "http://litellm:4000/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    for value in (None, ""):
        if value is None:
            monkeypatch.delenv("WEAVIATE_URL", raising=False)
        else:
            monkeypatch.setenv("WEAVIATE_URL", value)
        refuse = types.SimpleNamespace(connect_to_custom=lambda **_kw: (_ for _ in ()).throw(AssertionError))
        namespace = {"os": __import__("os"), "weaviate": refuse, "documents": [],
                     "ChatOpenAI": lambda **_kw: None, "OpenAIEmbeddings": lambda **_kw: None}
        for source in (setup, store, query):
            exec(compile(source, "02_langchain_rag", "exec"), namespace)  # noqa: S102
        assert namespace["wv_client"] is None
        assert "Weaviate not configured" in capsys.readouterr().out
    # A configured Weaviate is still connected.
    monkeypatch.setenv("WEAVIATE_URL", "http://weaviate:8080")
    connected = types.SimpleNamespace(is_ready=lambda: True)
    namespace["weaviate"] = types.SimpleNamespace(connect_to_custom=lambda **kw: connected)
    exec(compile(setup, "02_langchain_rag", "exec"), namespace)  # noqa: S102
    assert namespace["wv_client"] is connected


def test_rag_notebook_stores_documents_idempotently():
    """Every run inserted the same five documents again with server ids, so
    a re-run duplicated them and the RAG context repeated one sentence."""
    import json
    from pathlib import Path

    nb = json.loads((Path(__file__).resolve().parents[2]
                     / "services/jupyterhub/build/notebooks/02_langchain_rag.ipynb").read_text())
    store = next("".join(c["source"]) for c in nb["cells"] if "collection.data.insert" in "".join(c["source"]))
    assert "generate_uuid5(doc)" in store and "uuid=doc_id" in store and "data.exists(doc_id)" in store


def test_jupyter_origin_defaults_to_same_origin_and_spark_kill_is_off():
    """allow_origin '*' let another localhost page open a cookie-authenticated
    terminal; the Spark master UI accepted cross-site kill POSTs
    (2026-10-08 run, cycle 57)."""
    import yaml

    repo = Path(__file__).resolve().parents[2]
    manifest = yaml.safe_load((repo / "services/jupyterhub/service.yml").read_text())
    default = next(v["default"] for v in manifest["env"] if v["name"] == "JUPYTER_ALLOW_ORIGIN")
    assert default == ""
    assert "${JUPYTER_ALLOW_ORIGIN:-*}" not in (repo / "services/jupyterhub/compose.yml").read_text()
    spark = yaml.safe_load((repo / "services/spark/compose.yml").read_text())
    assert "-Dspark.ui.killEnabled=false" in spark["services"]["spark-master"]["environment"]["SPARK_MASTER_OPTS"]


import pytest


def _run_jupyter_startup(tmp_path, settings):
    import os
    import subprocess
    from types import SimpleNamespace

    home = tmp_path / "owned-jovyan"
    work = home / "work"
    examples = work / "examples"
    notebooks = home / "notebooks"
    examples.mkdir(parents=True)
    notebooks.mkdir()
    (work / ".env").write_text("previous environment\n")
    (work / ".env").chmod(0o644)
    (examples / "existing.ipynb").write_text("keep user copy")
    (notebooks / "existing.ipynb").write_text("replace user copy")
    (notebooks / "new.ipynb").write_text("new sample")
    script = tmp_path / "owned-startup.sh"
    script.write_text(STARTUP_FILE.read_text().replace("/home/jovyan", str(home)))
    assert "/home/jovyan" not in script.read_text()
    proof = tmp_path / "owned-launch.json"
    recorder = (
        "import json,os,sys; from pathlib import Path; "
        "Path(sys.argv[1]).write_text(json.dumps({'env':dict(os.environ),"
        "'argv':sys.argv[2:],'umask':os.umask(0o22)}))"
    )
    result = subprocess.run(
        ["/bin/bash", "-c", 'umask 027; exec "$@"', "owned-launch",
         "/bin/bash", str(script), sys.executable, "-c", recorder, str(proof),
         "--owned=two words", "literal$argument"],
        env={"PATH": os.defpath, "HOME": str(home), **settings},
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0, result.stderr
    launched = json.loads(proof.read_text())
    _assert_jupyter_startup_artifacts(result, work, launched)
    return SimpleNamespace(work=work, env=launched["env"])


def _assert_jupyter_startup_artifacts(result, work, launched):
    import stat

    examples = work / "examples"
    assert "Environment file created at" in result.stdout
    assert "Sample notebooks available in" in result.stdout
    assert "JupyterHub is ready!" in result.stdout
    assert stat.S_IMODE((work / ".env").stat().st_mode) == 0o600
    assert (examples / "existing.ipynb").read_text() == "keep user copy"
    assert (examples / "new.ipynb").read_text() == "new sample"
    assert launched["argv"] == ["--owned=two words", "literal$argument"]
    assert launched["umask"] == 0o27


def _first_litellm_notebook_client(started, monkeypatch):
    import os
    from types import SimpleNamespace
    from unittest.mock import patch

    notebook = json.loads((NOTEBOOK_DIR / "01_litellm_basics.ipynb").read_text())
    cell = next(cell for cell in notebook["cells"] if cell["cell_type"] == "code")
    cell_path = started.work / "owned-notebook-cell.py"
    cell_path.write_text("".join(cell["source"]))
    captured = {}
    monkeypatch.chdir(started.work)
    for name in ("LITELLM_API_KEY", "LITELLM_BASE_URL", "OPENAI_API_KEY", "OPENAI_API_BASE"):
        if name in started.env:
            monkeypatch.setenv(name, started.env[name])
        else:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("OWNED_UNSET_LITERAL", raising=False)
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(
        OpenAI=lambda **kwargs: captured.update(kwargs)))
    with patch.dict(os.environ):
        exec(compile(cell_path.read_text(), str(cell_path), "exec"), {})
    return captured


@pytest.mark.parametrize("key", [
    "sk-synthetic #suffix", "  sk-synthetic  ", '"sk-synthetic"', "'sk-synthetic'",
    "sk-\u0024{OWNED_UNSET_LITERAL}", r"sk-\t-literal", "sk-plain", "sk-no-space#suffix",
])
def test_startup_notebook_preserves_literal_litellm_key(tmp_path, monkeypatch, key):
    from utils.atomic_write import decode_env_assignment, render_env_assignment

    assignment = "LITELLM_MASTER_KEY=" + render_env_assignment("LITELLM_MASTER_KEY", key)
    assert decode_env_assignment(assignment, origin="owned-input")[1] == key
    started = _run_jupyter_startup(tmp_path, {
        "LITELLM_API_KEY": key, "LITELLM_BASE_URL": "http://owned-litellm.invalid:4000"})
    client = _first_litellm_notebook_client(started, monkeypatch)
    assert client["api_key"] == key
    assert client["base_url"] == "http://owned-litellm.invalid:4000/v1"
    assert {"OPENAI_API_BASE", "OPENAI_API_KEY"} <= started.env.keys()
    assert started.env["OPENAI_API_KEY"] == key
    assert started.env["OPENAI_API_BASE"] == client["base_url"]


@pytest.mark.parametrize(("settings", "expected"), [
    ({}, ("http://litellm:4000/v1", "")),
    ({"LITELLM_BASE_URL": "", "LITELLM_API_KEY": ""}, ("http://litellm:4000/v1", "")),
    ({"LITELLM_BASE_URL": "http://owned.invalid/v1"}, ("http://owned.invalid/v1/v1", "")),
    ({"LITELLM_BASE_URL": "http://owned.invalid/"}, ("http://owned.invalid//v1", "")),
    ({"OPENAI_API_BASE": "http://inherited.invalid/v1", "OPENAI_API_KEY": " own #key "},
     ("http://inherited.invalid/v1", " own #key ")),
    ({"OPENAI_API_BASE": "", "OPENAI_API_KEY": ""}, ("", "")),
    ({"OPENAI_API_BASE": "", "OPENAI_API_KEY": "inherited"}, ("", "inherited")),
    ({"OPENAI_API_BASE": "http://inherited.invalid", "OPENAI_API_KEY": ""},
     ("http://inherited.invalid", "")),
    ({"OPENAI_API_KEY": ""}, ("http://litellm:4000/v1", "")),
    ({"OPENAI_API_BASE": "", "LITELLM_API_KEY": "sk-own #key"}, ("", "sk-own #key")),
    ({"OPENAI_API_KEY": " own #key ", "LITELLM_BASE_URL": "http://owned.invalid"},
     ("http://owned.invalid/v1", " own #key ")),
    ({"OPENAI_API_BASE": "http://inherited.invalid", "LITELLM_API_KEY": "sk-plain"},
     ("http://inherited.invalid", "sk-plain")),
])
def test_startup_openai_alias_defaults_and_inherited_values(tmp_path, monkeypatch, settings, expected):
    started = _run_jupyter_startup(tmp_path, settings)
    client = _first_litellm_notebook_client(started, monkeypatch)
    assert (client["base_url"], client["api_key"]) == expected
    assert {"OPENAI_API_BASE", "OPENAI_API_KEY"} <= started.env.keys()
    assert (started.env["OPENAI_API_BASE"], started.env["OPENAI_API_KEY"]) == expected


def _invoke_notebook_cleanup_case(started, context, raises):
    if raises:
        with pytest.raises(RuntimeError, match="owned notebook failure"):
            _first_litellm_notebook_client(started, context)
    else:
        client = _first_litellm_notebook_client(started, context)
        assert client == {"base_url": "http://owned-cleanup.invalid:4000/v1",
                          "api_key": "sk-owned-cleanup"}


@pytest.mark.parametrize("case", [
    ("absent", False), ("inherited", False),
    ("absent", True), ("inherited", True),
])
def test_notebook_client_restores_dotenv_environment_on_success_and_failure(
    tmp_path, monkeypatch, case
):
    import os
    from dotenv import dotenv_values
    from unittest.mock import patch

    mode, raises = case
    started = _run_jupyter_startup(tmp_path, {
        "LITELLM_API_KEY": "sk-owned-cleanup",
        "LITELLM_BASE_URL": "http://owned-cleanup.invalid:4000"})
    if raises:
        notebook = json.loads((NOTEBOOK_DIR / "01_litellm_basics.ipynb").read_text())
        cell = next(cell for cell in notebook["cells"] if cell["cell_type"] == "code")
        cell["source"] = ["".join(cell["source"]) + "\nraise RuntimeError('owned notebook failure')\n"]
        private_notebooks = tmp_path / "raising-notebook"
        private_notebooks.mkdir()
        (private_notebooks / "01_litellm_basics.ipynb").write_text(json.dumps(notebook))
        monkeypatch.setitem(globals(), "NOTEBOOK_DIR", private_notebooks)

    # The outer scope cleans up even when this regression fails against an
    # unisolated helper. Compare inside it, after the helper's own teardown.
    with patch.dict(os.environ):
        with pytest.MonkeyPatch.context() as setup:
            for name in dotenv_values(started.work / ".env"):
                if mode == "absent":
                    setup.delenv(name, raising=False)
                else:
                    setup.setenv(name, "owned-inherited-" + name.lower())
            before = dict(os.environ)
            with pytest.MonkeyPatch.context() as context:
                _invoke_notebook_cleanup_case(started, context, raises)
            changed = sorted(
                name for name in before.keys() | os.environ.keys()
                if before.get(name) != os.environ.get(name)
            )
            assert changed == []
