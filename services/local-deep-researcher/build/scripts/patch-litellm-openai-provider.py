#!/usr/bin/env python3
"""Patch Local Deep Researcher to use LiteLLM through ChatOpenAI."""

from __future__ import annotations

import sys
from pathlib import Path


sys.stdout.reconfigure(line_buffering=True)

CONFIGURATION_PATH = Path("/app/src/ollama_deep_researcher/configuration.py")
GRAPH_PATH = Path("/app/src/ollama_deep_researcher/graph.py")


def _patch_configuration(text: str) -> str:
    if 'Literal["ollama", "lmstudio", "openai"]' in text:
        return text

    text = text.replace(
        'llm_provider: Literal["ollama", "lmstudio"] = Field(',
        'llm_provider: Literal["ollama", "lmstudio", "openai"] = Field(',
    )
    text = text.replace(
        'description="Provider for the LLM (Ollama or LMStudio)",',
        'description="Provider for the LLM (Ollama, LMStudio, or OpenAI-compatible)",',
    )
    text = text.replace(
        '''    lmstudio_base_url: str = Field(
        default="http://localhost:1234/v1",
        title="LMStudio Base URL",
        description="Base URL for LMStudio OpenAI-compatible API",
    )
''',
        '''    lmstudio_base_url: str = Field(
        default="http://localhost:1234/v1",
        title="LMStudio Base URL",
        description="Base URL for LMStudio OpenAI-compatible API",
    )
    openai_api_base: str = Field(
        default="http://litellm:4000/v1",
        title="OpenAI-compatible Base URL",
        description="Base URL for OpenAI-compatible APIs such as LiteLLM",
    )
    openai_api_key: str = Field(
        default="",
        title="OpenAI-compatible API Key",
        description="API key for OpenAI-compatible APIs such as LiteLLM",
    )
''',
    )
    return text


def _patch_graph(text: str) -> str:
    if "from langchain_openai import ChatOpenAI" not in text:
        text = text.replace(
            "from langchain_ollama import ChatOllama\n",
            "from langchain_ollama import ChatOllama\nfrom langchain_openai import ChatOpenAI\n",
        )

    text = _patch_get_llm(text)
    text = _patch_summarize_sources(text)
    return text


def _patch_get_llm(text: str) -> str:
    get_llm_start = text.index("def get_llm(")
    try:
        get_llm_end = text.index("\n# Nodes", get_llm_start)
    except ValueError:
        get_llm_end = text.index("\ndef ", get_llm_start + 1)
    get_llm = text[get_llm_start:get_llm_end]
    if 'configurable.llm_provider == "openai"' in get_llm:
        return text

    patched_get_llm = get_llm.replace(
        '''    if configurable.llm_provider == "lmstudio":
''',
        '''    if configurable.llm_provider == "openai":
        kwargs = {
            "base_url": configurable.openai_api_base,
            "api_key": configurable.openai_api_key or "not-needed",
            "model": configurable.local_llm,
            "temperature": 0,
        }
        if not configurable.use_tool_calling:
            kwargs["model_kwargs"] = {"response_format": {"type": "json_object"}}
        return ChatOpenAI(**kwargs)

    if configurable.llm_provider == "lmstudio":
''',
        1,
    )
    if patched_get_llm == get_llm:
        raise RuntimeError("could not find get_llm() lmstudio branch to patch")
    return text[:get_llm_start] + patched_get_llm + text[get_llm_end:]


def _patch_summarize_sources(text: str) -> str:
    try:
        summarize_start = text.index("def summarize_sources(")
    except ValueError:
        return text

    try:
        summarize_end = text.index("\ndef ", summarize_start + 1)
    except ValueError:
        summarize_end = len(text)
    summarize = text[summarize_start:summarize_end]
    if 'configurable.llm_provider == "openai"' in summarize:
        return text

    patched_summarize = summarize.replace(
        '''    if configurable.llm_provider == "lmstudio":
''',
        '''    if configurable.llm_provider == "openai":
        llm = ChatOpenAI(
            base_url=configurable.openai_api_base,
            api_key=configurable.openai_api_key or "not-needed",
            model=configurable.local_llm,
            temperature=0,
        )
    elif configurable.llm_provider == "lmstudio":
''',
        1,
    )
    if patched_summarize == summarize:
        raise RuntimeError("could not find summarize_sources() lmstudio branch to patch")
    return text[:summarize_start] + patched_summarize + text[summarize_end:]


def main() -> None:
    configuration = CONFIGURATION_PATH.read_text(encoding="utf-8")
    graph = GRAPH_PATH.read_text(encoding="utf-8")

    patched_configuration = _patch_configuration(configuration)
    patched_graph = _patch_graph(graph)

    if 'Literal["ollama", "lmstudio", "openai"]' not in patched_configuration:
        raise RuntimeError("could not patch Configuration.llm_provider for openai")
    get_llm_start = patched_graph.index("def get_llm(")
    get_llm_end = patched_graph.index("\n# Nodes", get_llm_start)
    get_llm = patched_graph[get_llm_start:get_llm_end]
    if 'configurable.llm_provider == "openai"' not in get_llm:
        raise RuntimeError("could not patch get_llm() for openai")
    if "def summarize_sources(" in patched_graph:
        summarize_start = patched_graph.index("def summarize_sources(")
        try:
            summarize_end = patched_graph.index("\ndef ", summarize_start + 1)
        except ValueError:
            summarize_end = len(patched_graph)
        summarize = patched_graph[summarize_start:summarize_end]
        if 'configurable.llm_provider == "openai"' not in summarize:
            raise RuntimeError("could not patch summarize_sources() for openai")

    if patched_configuration == configuration and patched_graph == graph:
        print("Local Deep Researcher: LiteLLM OpenAI provider patch already applied")
        return

    CONFIGURATION_PATH.write_text(patched_configuration, encoding="utf-8")
    GRAPH_PATH.write_text(patched_graph, encoding="utf-8")
    print("Local Deep Researcher: LiteLLM OpenAI provider patch applied")


if __name__ == "__main__":
    main()
