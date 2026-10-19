"""Zero-API-key live web search and news headlines tool powered by DuckDuckGo."""

from __future__ import annotations

import html
import json
import logging
import re
from typing import Any
import urllib.parse
import urllib.request

from jarvis.core.types import RiskTier, ToolSpec

logger = logging.getLogger(__name__)


def web_search(query: str, max_results: int = 5) -> dict[str, Any]:
    """Search the web via DuckDuckGo HTML without requiring any API keys.

    Args:
        query: The search term or question.
        max_results: Maximum number of search snippets to return (default: 5).

    Returns:
        Dictionary containing search status, query, results list, and formatted summary.
    """
    q = query.strip()
    if not q:
        return {"status": "error", "error": "Query string cannot be empty."}

    # DuckDuckGo HTML search endpoint
    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(q)}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            content = resp.read().decode("utf-8", errors="replace")

        # Parse results using regex over DuckDuckGo's lightweight HTML structure
        # Matches: <a class="result__snippet" ...>snippet</a>
        results = []
        
        # Pattern extracting result blocks
        pattern = re.compile(
            r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
            re.IGNORECASE | re.DOTALL,
        )
        title_pattern = re.compile(
            r'<a[^>]*class="[^"]*result__url[^"]*"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
            re.IGNORECASE | re.DOTALL,
        )

        snippets = pattern.findall(content)
        url_blocks = title_pattern.findall(content)

        for i in range(min(len(snippets), max_results)):
            clean_snip = html.unescape(re.sub(r"<[^>]+>", "", snippets[i])).strip()
            # Clean up snippet
            clean_snip = re.sub(r"\s+", " ", clean_snip)
            
            raw_url = ""
            if i < len(url_blocks):
                raw_href, _ = url_blocks[i]
                # DuckDuckGo wraps urls in /l/?uddg=...
                match_uddg = re.search(r"uddg=([^&]+)", raw_href)
                if match_uddg:
                    raw_url = urllib.parse.unquote(match_uddg.group(1))
                else:
                    raw_url = raw_href.strip()

            if clean_snip:
                results.append({
                    "snippet": clean_snip,
                    "url": raw_url,
                })

        # Fallback to DuckDuckGo Instant Answer API if HTML snippets were sparse
        if not results:
            api_url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(q)}&format=json&no_html=1&skip_disambig=1"
            req_api = urllib.request.Request(api_url, headers=headers)
            with urllib.request.urlopen(req_api, timeout=6.0) as api_resp:
                api_data = json.loads(api_resp.read().decode("utf-8", errors="replace"))
                abstract = api_data.get("AbstractText", "")
                source_url = api_data.get("AbstractURL", "")
                if abstract:
                    results.append({"snippet": abstract, "url": source_url})
                for related in api_data.get("RelatedTopics", [])[:max_results]:
                    txt = related.get("Text")
                    first_url = related.get("FirstURL", "")
                    if txt:
                        results.append({"snippet": txt, "url": first_url})

        if not results:
            return {
                "status": "success",
                "query": q,
                "results": [],
                "summary": f"No direct web search snippets found for '{q}'.",
            }

        summary_lines = [f"Found {len(results)} relevant web results for '{q}':"]
        for idx, res in enumerate(results, 1):
            url_part = f" ({res['url']})" if res.get("url") else ""
            summary_lines.append(f"{idx}. {res['snippet']}{url_part}")

        return {
            "status": "success",
            "query": q,
            "results": results,
            "summary": "\n".join(summary_lines),
        }

    except Exception as exc:
        logger.debug("Web search failed: %s", exc)
        return {
            "status": "error",
            "error": f"Live web search request failed: {exc}",
            "query": q,
        }


def get_latest_news(topic: str = "technology") -> dict[str, Any]:
    """Retrieve top recent headlines for a topic using DuckDuckGo news search."""
    t = topic.strip() if topic else "technology"
    search_query = f"{t} latest news today"
    return web_search(query=search_query, max_results=4)


def create_web_search_tool() -> ToolSpec:
    """Create ToolSpec for web_search."""
    return ToolSpec(
        name="web_search",
        description="Search the live web using DuckDuckGo (zero API key required) to answer questions, check current facts, documentation, or news.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query or question (e.g. 'latest space mission', 'Python 3.14 release date').",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of search result snippets to retrieve (default: 5).",
                },
            },
            "required": ["query"],
        },
        risk_tier=RiskTier.AUTO,
        handler=web_search,
        untrusted_output=True,
    )


def create_latest_news_tool() -> ToolSpec:
    """Create ToolSpec for get_latest_news."""
    return ToolSpec(
        name="get_latest_news",
        description="Retrieve recent news headlines on any topic (technology, science, world, business).",
        input_schema={
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "Topic or category (e.g. 'technology', 'world news', 'AI advancements').",
                },
            },
        },
        risk_tier=RiskTier.AUTO,
        handler=get_latest_news,
        untrusted_output=True,
    )
