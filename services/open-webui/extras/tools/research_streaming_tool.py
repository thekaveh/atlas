"""
title: Research Assistant (Enhanced)
author: Atlas
author_url: https://github.com/thekaveh/atlas
description: Enhanced research tool with progress tracking and detailed results
required_open_webui_version: 0.4.4
requirements: requests
version: 1.0.0
license: MIT
"""

import time
import requests
from typing import Dict, Any
from pydantic import BaseModel, Field


class Tools:
    class Valves(BaseModel):
        researcher_url: str = Field(
            default="http://local-deep-researcher:2024",
            description="Deep Researcher service URL",
        )
        timeout: int = Field(default=300, description="Max wait time in seconds")
        poll_interval: float = Field(
            default=3.0, description="Status check interval in seconds"
        )
        assistant_id: str = Field(
            default="ollama_deep_researcher",
            description="LangGraph assistant/graph id for Local Deep Researcher",
        )
        show_progress: bool = Field(
            default=True, description="Show research progress updates"
        )

    def __init__(self):
        self.valves = self.Valves()

    def research_with_progress(
        self, query: str, __user__: Dict[str, Any] = None
    ) -> str:
        """
        Enhanced research with progress tracking and detailed results
        """
        if not query.strip():
            return "❌ Please provide a research query"

        if not self.valves.show_progress:
            return "❌ Research tool is currently disabled"

        # Start research session
        try:
            result_parts = []
            result_parts.append(f"🚀 **Starting research:** {query}\n")

            session_id = self._start_research_session(query)
            if not session_id:
                return "❌ Failed to start research session"

            result_parts.append(f"📋 **Research session created:** `{session_id}`\n")

            # Track progress and get final results
            final_result = self._track_research_progress(session_id, query)
            result_parts.append(final_result)

            return "\n".join(result_parts)

        except Exception:
            return "❌ **Research failed unexpectedly. Please try again later.**"

    def _start_research_session(self, query: str) -> str:
        """Start a research session and return session ID"""
        try:
            # Create a new thread with unique metadata
            timestamp = int(time.time() * 1000)

            thread_resp = requests.post(
                f"{self.valves.researcher_url}/threads",
                json={
                    "metadata": {
                        "query": query,
                        "timestamp": timestamp,
                        "source": "open_webui_streaming_tool",
                    }
                },
                timeout=30,
            )

            if thread_resp.status_code != 200:
                return None

            thread_data = thread_resp.json()
            return thread_data.get("thread_id")

        except Exception:
            return None

    def _track_research_progress(self, thread_id: str, query: str) -> str:
        """Track research progress and return final results"""

        # Start the research run
        try:
            run_resp = requests.post(
                    f"{self.valves.researcher_url}/threads/{thread_id}/runs/wait",
                    json={
                        "assistant_id": self.valves.assistant_id,
                        "on_disconnect": "cancel",
                        "input": {
                            "research_topic": query  # Deep Researcher expects 'research_topic' not 'query'
                        },
                    "config": {
                        "configurable": {
                            "max_web_research_loops": 3,
                            "search_api": "searxng",
                        }
                    },
                },
                timeout=self.valves.timeout,
            )

            if run_resp.status_code != 200:
                return f"❌ **Research failed:** HTTP {run_resp.status_code}"

            # Get the final results
            result_data = run_resp.json()
            return self._format_langgraph_result(result_data, query, thread_id)

        except requests.exceptions.Timeout:
            return f"⏱️ **Research timed out** after {self.valves.timeout} seconds"
        except Exception:
            return "❌ **Research failed unexpectedly. Please try again later.**"

    def _format_langgraph_result(self, result: dict, query: str, thread_id: str) -> str:
        """Format LangGraph research results"""
        output = []

        title = result.get("title", f"Research Results: {query}")
        output.append(f"# {title}")

        # Handle different possible response structures
        if "running_summary" in result:
            output.append(f"\n## Research Report\n{result['running_summary']}")
        elif "final_report" in result:
            output.append(f"\n## Research Report\n{result['final_report']}")
        elif "report" in result:
            output.append(f"\n## Research Report\n{result['report']}")
        elif "content" in result:
            content = result["content"]
            if len(content) > 2000:
                content = content[:2000] + "\n\n[Content truncated for brevity...]"
            output.append(f"\n## Details\n{content}")

        # Extract sources if available
        sources = result.get("sources_gathered", result.get("sources", []))
        if sources:
            output.append("\n## Sources")
            for i, src in enumerate(sources[:5], 1):
                if isinstance(src, dict):
                    title = src.get("title", "Untitled")
                    url = src.get("url", "#")
                    output.append(f"{i}. [{title}]({url})")
                else:
                    output.append(f"{i}. {str(src)}")

        output.append(f"\n## Research Info")
        output.append(f"- Thread ID: {thread_id}")
        output.append(f"- Query: {query}")
        output.append(f"- Status: ✅ Completed")

        return "\n".join(output)
