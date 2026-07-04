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

    if 'configurable.llm_provider == "openai"' in text:
        return text

    text = text.replace(
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
    )
    return text


def main() -> None:
    configuration = CONFIGURATION_PATH.read_text(encoding="utf-8")
    graph = GRAPH_PATH.read_text(encoding="utf-8")

    patched_configuration = _patch_configuration(configuration)
    patched_graph = _patch_graph(graph)

    if 'Literal["ollama", "lmstudio", "openai"]' not in patched_configuration:
        raise RuntimeError("could not patch Configuration.llm_provider for openai")
    if 'configurable.llm_provider == "openai"' not in patched_graph:
        raise RuntimeError("could not patch get_llm() for openai")

    if patched_configuration == configuration and patched_graph == graph:
        print("Local Deep Researcher: LiteLLM OpenAI provider patch already applied")
        return

    CONFIGURATION_PATH.write_text(patched_configuration, encoding="utf-8")
    GRAPH_PATH.write_text(patched_graph, encoding="utf-8")
    print("Local Deep Researcher: LiteLLM OpenAI provider patch applied")


if __name__ == "__main__":
    main()
