"""
Regression guard: weaviate-init, LDR init-config.py, backend memory_service.py, and
ollama-pull must NOT query ``public.llms`` for model selection — they now read resolved
env vars (LITELLM_EMBEDDING_MODEL / LITELLM_DEFAULT_MODEL / OLLAMA_USER_MODELS) directly.

If any of these assertions fail it means someone re-introduced a DB query against
public.llms for model resolution inside one of these consumers.  That is the
pattern being removed in B5 (repoint consumers off public.llms to env vars).
"""

from pathlib import Path
import importlib.util
import ast
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def _load_ldr_provider_patcher():
    path = REPO_ROOT / "services/local-deep-researcher/build/scripts/patch-litellm-openai-provider.py"
    spec = importlib.util.spec_from_file_location("patch_litellm_openai_provider", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ldr_loop_graph(comparison="state.research_loop_count <= configurable.max_web_research_loops"):
    # Routing/increment contract from pinned upstream 38f769f84380f2065de76021ac7c5215f88aa39e.
    return f'''from langchain_ollama import ChatOllama

def get_llm(configurable):
    if configurable.llm_provider == "lmstudio":
        return ChatOpenAI(base_url=configurable.lmstudio_base_url)
    return ChatOllama(model=configurable.local_llm)

# Nodes
def route_research(state, config):
    configurable = Configuration.from_runnable_config(config)
    if {comparison}:
        return "web_research"
    else:
        return "finalize_summary"
'''


def _ldr_patched_router(graph):
    patched = _load_ldr_provider_patcher()._patch_graph(graph)
    route = next(node for node in ast.parse(patched).body
                 if isinstance(node, ast.FunctionDef) and node.name == "route_research")
    namespace = {"Configuration": SimpleNamespace(from_runnable_config=lambda cfg:
                 SimpleNamespace(max_web_research_loops=cfg["configurable"]["max_web_research_loops"]))}
    exec(compile(ast.Module(body=[route], type_ignores=[]), "patched-ldr-router", "exec"), namespace)
    return namespace["route_research"]


def test_weaviate_init_no_public_llms():
    """init-weaviate.sh must not query public.llms for the embedding model."""
    text = _read("services/weaviate/init/scripts/init-weaviate.sh")
    assert "public.llms" not in text, (
        "init-weaviate.sh still references public.llms — "
        "the embedding model must come from LITELLM_EMBEDDING_MODEL env var."
    )


def test_ldr_init_config_no_public_llms():
    """LDR init-config.py must not query public.llms for the content model."""
    text = _read("services/local-deep-researcher/build/scripts/init-config.py")
    assert "public.llms" not in text, (
        "init-config.py still references public.llms — "
        "the content model must come from LITELLM_DEFAULT_MODEL env var."
    )


def test_backend_memory_service_no_public_llms():
    """memory_service.py _get_extraction_model must not query public.llms."""
    text = _read("services/backend/app/app/memory_service.py")
    assert "public.llms" not in text, (
        "memory_service.py still references public.llms for model selection — "
        "_get_extraction_model must resolve via LITELLM_DEFAULT_MODEL env var only."
    )


def test_weaviate_compose_passes_embedding_model():
    """weaviate/compose.yml must inject LITELLM_EMBEDDING_MODEL into weaviate-init."""
    text = _read("services/weaviate/compose.yml")
    assert "LITELLM_EMBEDDING_MODEL" in text, (
        "weaviate/compose.yml does not pass LITELLM_EMBEDDING_MODEL to weaviate-init."
    )


def test_ldr_compose_passes_default_model():
    """local-deep-researcher/compose.yml must inject LITELLM_DEFAULT_MODEL."""
    text = _read("services/local-deep-researcher/compose.yml")
    assert "LITELLM_DEFAULT_MODEL" in text, (
        "local-deep-researcher/compose.yml does not pass LITELLM_DEFAULT_MODEL."
    )


def test_ldr_litellm_openai_provider_patch_contract():
    """LDR must patch upstream so LiteLLM's OpenAI-compatible API is supported."""
    patch = _read("services/local-deep-researcher/build/scripts/patch-litellm-openai-provider.py")
    init_config = _read("services/local-deep-researcher/build/scripts/init-config.py")
    entrypoint = _read("services/local-deep-researcher/build/scripts/docker-entrypoint.sh")
    readme = _read("services/local-deep-researcher/README.md")

    assert 'Literal["ollama", "lmstudio", "openai"]' in patch
    assert "from langchain_openai import ChatOpenAI" in patch
    assert "configurable.openai_api_base" in patch
    assert "configurable.openai_api_key" in patch
    assert "patch-litellm-openai-provider.py" in entrypoint
    assert entrypoint.index("patch-litellm-openai-provider.py") < entrypoint.index("init-config.py")
    assert "LLM_PROVIDER=openai" in init_config
    assert "OPENAI_API_BASE" in init_config
    assert "OPENAI_API_KEY" in init_config
    assert "assistant_id=ollama_deep_researcher" in readme
    assert '"assistant_id":"agent"' not in readme


def test_ldr_litellm_openai_provider_patch_is_scoped_to_graph_helpers():
    """The graph patch must not corrupt summarize_sources with a get_llm return."""
    patcher = _load_ldr_provider_patcher()
    graph = '''from langchain_ollama import ChatOllama


def get_llm(configurable):
    if configurable.llm_provider == "lmstudio":
        return ChatOpenAI(base_url=configurable.lmstudio_base_url)
    return ChatOllama(model=configurable.local_llm)


# Nodes
def summarize_sources(state, config):
    configurable = Configuration.from_runnable_config(config)
    if configurable.llm_provider == "lmstudio":
        llm = ChatOpenAI(base_url=configurable.lmstudio_base_url)
    else:
        llm = ChatOllama(model=configurable.local_llm)
    result = llm.invoke("summarize")
    return {"summary": result.content}


def reflect_on_summary(state, config):
    return {}
'''

    patched = patcher._patch_graph(graph)

    get_llm = patched[patched.index("def get_llm("):patched.index("\n# Nodes")]
    summarize = patched[patched.index("def summarize_sources("):patched.index("\ndef reflect_on_summary")]
    assert "from langchain_openai import ChatOpenAI" in patched
    assert get_llm.count('configurable.llm_provider == "openai"') == 1
    assert summarize.count('configurable.llm_provider == "openai"') == 1
    assert "return ChatOpenAI(**kwargs)" in get_llm
    assert "return ChatOpenAI(**kwargs)" not in summarize
    assert "result = llm.invoke" in summarize


def test_ollama_pull_no_public_llms():
    """pull.sh must not query public.llms — model list comes from env vars."""
    text = _read("services/ollama/pull/scripts/pull.sh")
    assert "public.llms" not in text, (
        "pull.sh still references public.llms — "
        "the model list must come from OLLAMA_USER_MODELS + OLLAMA_CUSTOM_MODELS env vars."
    )


def test_env_example_ollama_user_models_is_default_trio():
    """OLLAMA_USER_MODELS in .env.example must equal the catalog's default-active
    Ollama model CSV so a fresh install pulls the right default set without
    running the wizard.
    """
    import sys
    from pathlib import Path

    # Add bootstrapper to sys.path so llm_catalog is importable.
    bootstrapper_dir = REPO_ROOT / "bootstrapper"
    if str(bootstrapper_dir) not in sys.path:
        sys.path.insert(0, str(bootstrapper_dir))

    from utils.llm_catalog import default_active_names  # noqa: PLC0415

    expected_csv = ",".join(default_active_names("ollama"))
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

    # Find the OLLAMA_USER_MODELS line.
    for line in env_example.splitlines():
        if line.startswith("OLLAMA_USER_MODELS="):
            actual_csv = line.split("=", 1)[1]
            assert actual_csv == expected_csv, (
                f"OLLAMA_USER_MODELS in .env.example ({actual_csv!r}) does not match "
                f"the catalog's default-active set ({expected_csv!r}). "
                f"Re-run: uv run --project bootstrapper python -m services.env_assembler"
            )
            return

    raise AssertionError(
        "OLLAMA_USER_MODELS line not found in .env.example. "
        "Re-run: uv run --project bootstrapper python -m services.env_assembler"
    )


def test_backend_comfyui_routes_no_public_comfyui_models():
    """GET /comfyui/db/models must read the manifest, not public.comfyui_models.

    The write routes (POST/PUT/DELETE /comfyui/db/models) have been removed
    entirely; this assertion also covers them by checking the whole file.
    If this fails, someone has re-introduced a DB query against
    public.comfyui_models in the backend's comfyui endpoints.
    """
    text = _read("services/backend/app/app/main.py")
    assert "public.comfyui_models" not in text, (
        "main.py still references public.comfyui_models — "
        "GET /comfyui/db/models must read the manifest YAML, not the DB; "
        "the POST/PUT/DELETE write routes must be removed."
    )


def test_weaviate_init_no_double_prefix():
    """init-weaviate.sh must write the model through without re-prepending 'ollama/'."""
    text = _read("services/weaviate/init/scripts/init-weaviate.sh")
    # The old code wrote: LITELLM_EMBEDDING_MODEL=ollama/$embedding_model
    # where $embedding_model was a bare name.  The new code writes the env
    # value through unchanged.  A double-prefix write would look like
    # "ollama/ollama/" — assert that pattern is absent.
    assert "ollama/ollama/" not in text, (
        "init-weaviate.sh has a double ollama/ prefix — "
        "LITELLM_EMBEDDING_MODEL is already fully-qualified; do not re-prepend."
    )
    # And confirm the env var is used as the source (not hardcoded bare name).
    assert "LITELLM_EMBEDDING_MODEL" in text, (
        "init-weaviate.sh must read LITELLM_EMBEDDING_MODEL to source the model."
    )


def test_ldr_run_config_wins_for_loops_and_search_api_only():
    """Container SEARCH_API/MAX_WEB_RESEARCH_LOOPS must not override per-run values."""
    patcher = _load_ldr_provider_patcher()
    upstream = (
        "import os\n\n\nclass Configuration(BaseModel):\n"
        "        raw_values: dict[str, Any] = {\n"
        "            name: os.environ.get(name.upper(), configurable.get(name))\n"
        "            for name in cls.model_fields.keys()\n"
        "        }\n"
    )
    patched = patcher._patch_run_config_precedence(upstream)
    assert patcher._patch_run_config_precedence(patched) == patched
    assert '_ATLAS_RUN_CONFIG_FIRST = frozenset({"max_web_research_loops", "search_api"})' in patched
    assert "if name in _ATLAS_RUN_CONFIG_FIRST" in patched
    entry = _read("services/local-deep-researcher/build/scripts/patch-litellm-openai-provider.py")
    assert "_patch_run_config_precedence(_patch_configuration(configuration))" in entry


@pytest.mark.parametrize("max_loops", [1, 2, 3, 10])
def test_ldr_completed_iterations_honor_requested_maximum(max_loops):
    route = _ldr_patched_router(_ldr_loop_graph())
    state = SimpleNamespace(research_loop_count=0)
    config = {"configurable": {"max_web_research_loops": max_loops}}
    # web_research increments the completed count before reflect_on_summary routes.
    for _ in range(max_loops + 2):
        state.research_loop_count += 1
        if route(state, config) == "finalize_summary":
            break
    assert state.research_loop_count == max_loops


@pytest.mark.parametrize("count, maximum, expected", [
    (0, 1, "web_research"), (1, 3, "web_research"),
    (3, 1, "finalize_summary"), (11, 10, "finalize_summary"),
])
def test_ldr_loop_routing_preserves_other_boundaries(count, maximum, expected):
    route = _ldr_patched_router(_ldr_loop_graph())
    assert route(SimpleNamespace(research_loop_count=count),
                 {"configurable": {"max_web_research_loops": maximum}}) == expected


def test_ldr_loop_patch_is_idempotent_and_preserves_conforming_source():
    patcher = _load_ldr_provider_patcher()
    conforming = _ldr_loop_graph("state.research_loop_count < configurable.max_web_research_loops")
    once = patcher._patch_graph(conforming)
    assert patcher._patch_graph(once) == once
    assert _ldr_patched_router(conforming)(SimpleNamespace(research_loop_count=3),
            {"configurable": {"max_web_research_loops": 3}}) == "finalize_summary"


def test_ldr_loop_patch_rejects_unknown_routing_comparison():
    with pytest.raises(RuntimeError, match="research loop"):
        _load_ldr_provider_patcher()._patch_graph(
            _ldr_loop_graph("state.research_loop_count == configurable.max_web_research_loops"))
