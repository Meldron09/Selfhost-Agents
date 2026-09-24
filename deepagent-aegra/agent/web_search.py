"""The `web-search` subagent: Tavily-backed web search + page fetch.

Mirrors `agent-runtime`'s `deep-research` subagent in shape (numbered method,
explicit final-message contract, explicit no-fabrication rule) but is
deliberately simpler: Tavily only, no `SEARCH_PROVIDER` dispatch (issue #13's
spec, Out of Scope: "Any Bing/Azure/Foundry search or model integration --
Ollama-only model plane, Tavily-only search").

Always registered structurally (agent/subagents.py), regardless of whether a
run actually has web search turned on -- per-run availability is a runtime
config check performed by `WebSearchGateMiddleware`
(agent/web_search_gate.py), not a build-time decision about whether this
module gets imported at all.
"""
from __future__ import annotations

import os
import re
from html import unescape
from typing import Any

import httpx
from deepagents.middleware.subagents import SubAgent
from langchain_core.tools import tool

SEARCH_TIMEOUT = 45
FETCH_TIMEOUT = 45
MAX_FETCH_CHARS = 60_000


def _format_results(results: list[dict[str, Any]]) -> str:
    """One block per result: title, URL, then its extract."""
    blocks = []
    for result in results:
        title = result.get("title") or "(untitled)"
        url = result.get("url") or ""
        content = (result.get("content") or "").strip()
        header = f"{title}\n{url}"
        blocks.append(f"{header}\n{content}" if content else header)
    return "\n\n---\n\n".join(blocks)


@tool
def web_search(query: str, max_results: int = 5) -> str:
    """Search the web for current information via Tavily.

    Returns one block per result with the page title, its full URL, and an
    extract. Use `fetch_url` on a returned URL to read that page in full
    before relying on any figure from it.
    """
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return "web_search is not configured: TAVILY_API_KEY is not set."

    try:
        response = httpx.post(
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "query": query,
                "max_results": max_results,
                # advanced -> deeper crawl and longer extracts, so there is a
                # citable page to fetch rather than just a search summary.
                "search_depth": "advanced",
                "include_answer": False,
            },
            timeout=SEARCH_TIMEOUT,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return f"Tavily returned HTTP {exc.response.status_code}: {exc.response.text[:400]}"
    except httpx.HTTPError as exc:
        return f"Tavily request failed: {exc}"

    results = response.json().get("results", [])
    return _format_results(results) or f"No results for {query!r}."


def _strip_html(html: str) -> str:
    """Reduce HTML to readable text without pulling in a parser dependency."""
    html = re.sub(r"(?is)<(script|style|noscript|svg|head)\b.*?</\1>", " ", html)
    html = re.sub(r"(?is)<!--.*?-->", " ", html)
    html = re.sub(r"(?i)<(br|/p|/div|/li|/tr|/h[1-6])\s*/?>", "\n", html)
    text = unescape(re.sub(r"(?s)<[^>]+>", " ", html))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


@tool
def fetch_url(url: str) -> str:
    """Fetch one web page and return its readable text.

    Use after `web_search` to read a source properly -- quoting a figure from
    a search extract is how a synthesis ends up citing something the page
    never actually said.
    """
    try:
        response = httpx.get(
            url,
            timeout=FETCH_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "deepagent-aegra/0.1 (+self-hosted deepagents demo)"},
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return f"{url} returned HTTP {exc.response.status_code}."
    except httpx.HTTPError as exc:
        return f"Failed to fetch {url}: {exc}"

    content_type = response.headers.get("content-type", "")
    if not any(t in content_type for t in ("text/html", "text/plain", "json", "xml")):
        return f"{url} is {content_type or 'an unknown type'} ({len(response.content)} bytes), not text."

    text = _strip_html(response.text) if "html" in content_type else response.text
    if len(text) > MAX_FETCH_CHARS:
        text = text[:MAX_FETCH_CHARS] + f"\n\n[truncated at {MAX_FETCH_CHARS} characters]"
    return f"{url}\n\n{text}"


SYSTEM_PROMPT = """You are the web-search subagent. You are given one research question and you answer it from real web sources.

Method:
1. Break the question into the specific sub-questions that together answer it.
2. `web_search` for each. Prefer primary sources over aggregators when the question turns on a specific fact or figure.
3. `fetch_url` any page you intend to rely on before quoting it -- a search extract is a summary and is frequently wrong about specifics.
4. If `web_search` reports it isn't configured (no TAVILY_API_KEY), say so plainly and stop. Do not answer from memory instead.

Return a synthesis, not a transcript. Your final message must contain:
- The answer, with a source URL for every claim that came from the web.
- An explicit "not found" for anything you could not source. Never estimate, interpolate, or invent a fact you did not actually read on a page -- a labelled gap is useful, a fabricated one poisons the whole answer.

Keep the synthesis tight and grounded only in what you actually fetched."""


def build_web_search_subagent() -> SubAgent:
    """The `SubAgent` spec the orchestrator's `task` tool delegates to.

    Always registered (agent/subagents.py) -- `WebSearchGateMiddleware`
    refuses the delegation per-run when it's off, rather than this subagent
    ever being conditionally left out of the roster.
    """
    return {
        "name": "web-search",
        "description": (
            "Answer one research question from the web. Use for anything "
            "beyond a single trivial fact; give it one self-contained "
            "question per call."
        ),
        "system_prompt": SYSTEM_PROMPT,
        "tools": [web_search, fetch_url],
    }
