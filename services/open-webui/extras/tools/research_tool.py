"""
title: Research Assistant
author: Atlas
author_url: https://github.com/thekaveh/atlas
description: Web research tool for information gathering
required_open_webui_version: 0.4.4
requirements: requests
version: 1.4.0
license: MIT
"""

import json
import time
import requests
from pydantic import BaseModel, Field


class Tools:
    class Valves(BaseModel):
        researcher_url: str = Field(
            default="http://local-deep-researcher:2024",
            description="Deep Researcher service URL",
        )
        timeout: int = Field(
            default=900,
            description="Max wait time in seconds (15 minutes for research completion)",
        )
        search_api: str = Field(
            default="searxng", description="Search API to use (searxng or duckduckgo)"
        )
        assistant_id: str = Field(
            default="ollama_deep_researcher",
            description="LangGraph assistant/graph id for Local Deep Researcher",
        )
        max_loops: int = Field(default=3, description="Maximum research loops")
        enable_tool: bool = Field(default=True, description="Enable this research tool")

    def __init__(self):
        self.valves = self.Valves()

    def research(self, query: str):
        """
        Research a topic using web search and AI analysis.

        :param query: The topic or question to research
        :return: Research findings with sources
        """

        if not self.valves.enable_tool:
            return str(
                "❌ Research tool is currently disabled. Enable it in tool settings if needed."
            )

        if not query:
            return str("❌ Please provide a research query.")

        try:
            # Create a new thread with unique metadata
            timestamp = int(time.time() * 1000)  # millisecond timestamp for uniqueness

            thread_resp = requests.post(
                f"{self.valves.researcher_url}/threads",
                json={
                    "metadata": {
                        "query": query,
                        "timestamp": timestamp,
                        "source": "open_webui_research_tool",
                    }
                },
                timeout=30,
            )

            if thread_resp.status_code != 200:
                return f"❌ Failed to create thread: HTTP {thread_resp.status_code}"

            thread_data = thread_resp.json()
            thread_id = thread_data.get("thread_id")

            if not thread_id:
                return "❌ Failed to create research thread."

            # Start research run with correct input format and timeout handling
            try:
                resp = requests.post(
                    f"{self.valves.researcher_url}/threads/{thread_id}/runs/wait",
                    json={
                        "assistant_id": self.valves.assistant_id,
                        "on_disconnect": "cancel",
                        "input": {
                            "research_topic": query  # Deep Researcher expects 'research_topic' not 'query'
                        },
                        "config": {
                            "configurable": {
                                "max_web_research_loops": min(self.valves.max_loops, 3),
                                "search_api": self.valves.search_api,
                            }
                        },
                    },
                    timeout=self.valves.timeout,
                )
            except requests.exceptions.Timeout:
                return str(
                    f"❌ Research timed out after {self.valves.timeout}s (15 minutes). Atlas requested LangGraph cancellation on disconnect; check the research service logs before retrying."
                )

            if resp.status_code != 200:
                return f"❌ Research failed with HTTP {resp.status_code}."

            # For LangGraph API, the response is the final result
            try:
                result_data = resp.json()
            except Exception:
                return "❌ Research returned an invalid response."

            # ULTRA-SAFE: Force immediate plain text conversion to prevent [object Object]
            # Convert response to plain text immediately - no complex object handling
            try:
                # Simple text extraction approach
                if isinstance(result_data, dict):
                    # Extract content using simple string operations
                    content_text = ""

                    # Look for common content fields and extract as text
                    for key in [
                        "running_summary",
                        "final_report",
                        "report",
                        "content",
                        "summary",
                        "result",
                        "answer",
                    ]:
                        if key in result_data and result_data[key]:
                            content_text = str(result_data[key])
                            break

                    if content_text:
                        # Return simple formatted text
                        simple_result = f"# Research Results: {query}\n\n{content_text}\n\n---\nResearch completed successfully ✅"
                    else:
                        # Fallback: JSON as text
                        simple_result = f"# Research Results: {query}\n\n```json\n{json.dumps(result_data, indent=2, default=str)}\n```\n\n---\nResearch completed ✅"
                else:
                    # Non-dict response: convert to text
                    simple_result = f"# Research Results: {query}\n\n{str(result_data)}\n\n---\nResearch completed ✅"

                # CRITICAL: Return as basic string literal - no complex types
                return simple_result

            except Exception:
                return "Research completed, but its result could not be formatted."

        except requests.exceptions.ConnectionError:
            return str(
                "❌ Cannot connect to research service. Please check if the backend is running."
            )
        except requests.exceptions.Timeout:
            return str(
                f"❌ Research service timed out after {self.valves.timeout}s (15 minutes). Service may be overloaded - try again later or increase timeout in settings."
            )
        except Exception:
            return "❌ Research failed unexpectedly. Please try again later."
