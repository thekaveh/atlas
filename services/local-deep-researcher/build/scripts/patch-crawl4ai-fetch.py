#!/usr/bin/env python3
"""Patch Local Deep Researcher to use Crawl4AI for full-page extraction."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path


sys.stdout.reconfigure(line_buffering=True)

UTILS_PATH = Path("/app/src/ollama_deep_researcher/utils.py")


def _crawl4ai_fetch_replacement() -> str:
    return '''
def _crawl4ai_markdown_from_result(result: dict) -> Optional[str]:
    markdown = result.get("markdown") or result.get("fit_markdown")
    if isinstance(markdown, dict):
        markdown = (
            markdown.get("raw_markdown")
            or markdown.get("markdown")
            or markdown.get("fit_markdown")
        )
    if markdown:
        return str(markdown)
    return None


def crawl4ai_fetch_raw_content(url: str) -> Optional[str]:
    """
    Fetch rendered markdown through Atlas Crawl4AI.

    Failures are intentionally per-URL soft failures: the research loop should
    keep the search snippet rather than aborting the whole run.
    """
    endpoint = os.getenv("CRAWL4AI_ENDPOINT", "").rstrip("/")
    token = os.getenv("CRAWL4AI_API_TOKEN", "")
    timeout = float(os.getenv("CRAWL4AI_TIMEOUT_SECONDS", "30"))
    max_chars = int(os.getenv("CRAWL4AI_MAX_CHARS", "60000"))

    if not endpoint or not token:
        print("Warning: Crawl4AI full-page mode selected but endpoint/token is missing")
        return None

    try:
        headers = {"Authorization": f"Bearer {token}"}
        request_body = {"urls": [url], "priority": 10}
        with httpx.Client(timeout=timeout) as client:
            response = client.post(f"{endpoint}/crawl", json=request_body, headers=headers)
            response.raise_for_status()
            response_body = response.json()

        results = response_body.get("results") or []
        if not results:
            print(f"Warning: Crawl4AI returned no results for {url}")
            return None

        first = results[0]
        if not first.get("success", False):
            print(f"Warning: Crawl4AI failed for {url}: {first.get('error_message')}")
            return None

        markdown = _crawl4ai_markdown_from_result(first)
        if not markdown:
            print(f"Warning: Crawl4AI returned no markdown for {url}")
            return None
        return markdown[:max_chars]
    except Exception as e:
        print(f"Warning: Failed to fetch full page content via Crawl4AI for {url}: {str(e)}")
        return None


def fetch_raw_content(url: str) -> Optional[str]:
    return crawl4ai_fetch_raw_content(url)

'''


def main() -> None:
    mode = os.getenv("LOCAL_DEEP_RESEARCHER_FULL_PAGE_MODE", "disabled").strip().lower()
    if mode != "crawl4ai":
        print(f"Local Deep Researcher: Crawl4AI fetch patch skipped (mode={mode})")
        return

    text = UTILS_PATH.read_text(encoding="utf-8")
    if "def crawl4ai_fetch_raw_content" in text:
        print("Local Deep Researcher: Crawl4AI fetch patch already applied")
        return

    patched, count = re.subn(
        r"(?ms)^def fetch_raw_content\\(url: str\\) -> Optional\\[str\\]:.*?(?=^@traceable\\n)",
        _crawl4ai_fetch_replacement(),
        text,
        count=1,
    )
    if count != 1:
        raise RuntimeError("could not locate upstream fetch_raw_content() seam")

    UTILS_PATH.write_text(patched, encoding="utf-8")
    print("Local Deep Researcher: Crawl4AI fetch patch applied")


if __name__ == "__main__":
    main()
