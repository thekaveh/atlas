"""
title: Memory Assistant
author: Atlas
author_url: https://github.com/thekaveh/atlas
description: Persistent memory - remembers facts about users across conversations
required_open_webui_version: 0.4.4
requirements: requests
version: 1.0.0
license: MIT
"""

import os
import requests
from pydantic import BaseModel, Field


class Tools:
    class Valves(BaseModel):
        backend_url: str = Field(
            default="http://backend:8000", description="Backend API URL"
        )
        enable_tool: bool = Field(default=True, description="Enable this memory tool")
        max_recall_results: int = Field(
            default=10, description="Maximum number of memories to recall"
        )
        timeout: int = Field(default=120, description="Request timeout in seconds")

    def __init__(self):
        self.valves = self.Valves()

    @staticmethod
    def _backend_headers() -> dict[str, str]:
        token = (os.getenv("BACKEND_OPEN_WEBUI_API_TOKEN") or "").strip()
        return {"Authorization": f"Bearer {token}"} if token else {}

    def remember(self, conversation: str, __user__: dict | None = None) -> str:
        """
        Extract and store memories from a conversation. Use this when the user
        asks you to remember something or when important facts are shared.

        :param conversation: The conversation text to extract memories from
        :return: Extracted memory facts
        """
        __user__ = __user__ or {}
        if not self.valves.enable_tool:
            return "Memory tool is currently disabled."

        if not conversation:
            return "Please provide conversation text to extract memories from."

        user_id = __user__.get("id", "")
        if not user_id:
            return "Error: User ID not available. Please ensure you are logged in."

        try:
            messages = [{"role": "user", "content": conversation}]

            response = requests.post(
                f"{self.valves.backend_url}/memory/extract",
                headers=self._backend_headers(),
                json={
                    "user_id": user_id,
                    "messages": messages,
                    "namespace": "default",
                },
                timeout=self.valves.timeout,
            )
            response.raise_for_status()
            result = response.json()

            facts = result.get("facts", [])
            if not facts:
                return "No new facts were extracted from the conversation."

            output_lines = [f"Extracted {len(facts)} memory fact(s):"]
            for fact in facts:
                output_lines.append(
                    f"- [{fact.get('fact_type', 'observation')}] "
                    f"{fact.get('content', '')} "
                    f"(confidence: {fact.get('confidence', 0):.1%})"
                )

            return "\n".join(output_lines)

        except requests.exceptions.ConnectionError:
            return (
                "Could not connect to the memory service. "
                "Please check if the Backend is running."
            )
        except Exception:
            return "Memory extraction failed. Please try again later."

    def recall(self, query: str, __user__: dict | None = None) -> str:
        """
        Recall relevant memories for a given topic or question. Use this when
        the user asks what you remember about something.

        :param query: The topic or question to recall memories about
        :return: Relevant memory facts
        """
        __user__ = __user__ or {}
        if not self.valves.enable_tool:
            return "Memory tool is currently disabled."

        if not query:
            return "Please provide a query to recall memories about."

        user_id = __user__.get("id", "")
        if not user_id:
            return "Error: User ID not available. Please ensure you are logged in."

        try:
            response = requests.post(
                f"{self.valves.backend_url}/memory/recall",
                headers=self._backend_headers(),
                json={
                    "user_id": user_id,
                    "query": query,
                    "namespace": "default",
                    "limit": self.valves.max_recall_results,
                    "min_confidence": 0.5,
                },
                timeout=self.valves.timeout,
            )
            response.raise_for_status()
            result = response.json()

            memories = result.get("memories", [])
            summary = result.get("context_summary")

            if not memories:
                return "No relevant memories found for this query."

            output_lines = [f"Found {len(memories)} relevant memory(ies):"]
            for mem in memories:
                output_lines.append(
                    f"- [{mem.get('fact_type', 'observation')}] "
                    f"{mem.get('content', '')} "
                    f"(confidence: {mem.get('confidence', 0):.1%})"
                )

            if summary:
                output_lines.append(f"\nSummary: {summary}")

            return "\n".join(output_lines)

        except requests.exceptions.ConnectionError:
            return (
                "Could not connect to the memory service. "
                "Please check if the Backend is running."
            )
        except Exception:
            return "Memory recall failed. Please try again later."

    def forget(self, memory_id: str, __user__: dict | None = None) -> str:
        """
        Delete a specific memory by its ID. Use this when the user wants
        to remove a stored memory.

        :param memory_id: The UUID of the memory to delete
        :return: Confirmation message
        """
        __user__ = __user__ or {}
        if not self.valves.enable_tool:
            return "Memory tool is currently disabled."

        if not memory_id:
            return "Please provide a memory ID to delete."

        user_id = __user__.get("id", "")
        if not user_id:
            return "Error: User ID not available. Please ensure you are logged in."

        try:
            response = requests.delete(
                f"{self.valves.backend_url}/memory/{memory_id}",
                headers=self._backend_headers(),
                params={"user_id": user_id},
                timeout=self.valves.timeout,
            )
            response.raise_for_status()

            return f"Memory {memory_id} has been deleted successfully."

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                return f"Memory {memory_id} not found."
            return f"Memory deletion failed with HTTP {e.response.status_code}."
        except requests.exceptions.ConnectionError:
            return (
                "Could not connect to the memory service. "
                "Please check if the Backend is running."
            )
        except Exception:
            return "Memory deletion failed. Please try again later."

    def list_memories(self, __user__: dict | None = None) -> str:
        """
        List all stored memories for the current user. Use this when the user
        wants to see everything that has been remembered about them.

        :return: List of all active memory facts
        """
        __user__ = __user__ or {}
        if not self.valves.enable_tool:
            return "Memory tool is currently disabled."

        user_id = __user__.get("id", "")
        if not user_id:
            return "Error: User ID not available. Please ensure you are logged in."

        try:
            response = requests.get(
                f"{self.valves.backend_url}/memory/user/{user_id}",
                headers=self._backend_headers(),
                params={"namespace": "default", "limit": 50},
                timeout=self.valves.timeout,
            )
            response.raise_for_status()
            result = response.json()

            memories = result.get("memories", [])
            total = result.get("total", 0)

            if not memories:
                return "No memories stored yet."

            output_lines = [f"You have {total} stored memory(ies):"]
            for mem in memories:
                output_lines.append(
                    f"- [{mem.get('fact_type', 'observation')}] "
                    f"{mem.get('content', '')} "
                    f"(id: {(mem.get('id') or 'N/A')[:8]}..., "
                    f"confidence: {mem.get('confidence', 0):.1%})"
                )

            return "\n".join(output_lines)

        except requests.exceptions.ConnectionError:
            return (
                "Could not connect to the memory service. "
                "Please check if the Backend is running."
            )
        except Exception:
            return "Memory listing failed. Please try again later."
