"""
web_search.py — Unified web search system for Amigo.
Beast-mode search + YouTube resolution + Google search + query cleaning.
"""

import asyncio
import hashlib
import html
import json
import logging
import re
import time
import urllib.parse
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("amigo.web_search")

# ──────────────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────────────

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Search engine endpoints (free, no API key required)
SEARCH_ENGINES = {
    "ddgs": {
        "url": "",  # Uses DDGS library directly
        "method": "LIBRARY",
        "parser": "ddgs_lib",
        "weight": 1.2,
    },
    "searxng": {
        "url": "https://searx.be/search?q={query}&format=json",
        "method": "GET",
        "parser": "searxng_json",
        "weight": 1.1,
    },
    "bing": {
        "url": "https://www.bing.com/search?q={query}",
        "method": "GET",
        "parser": "bing_html",
        "weight": 0.9,
    },
}

# Content extraction settings
MAX_CONTENT_LENGTH = 3000
TOP_K_RESULTS_TO_FETCH = 3
TOP_K_FINAL_SNIPPETS = 4
CACHE_TTL_SECONDS = 3600  # 1 hour
REQUEST_TIMEOUT = 5
FETCH_TIMEOUT = 6

# Domain credibility scores (higher = more trustworthy)
DOMAIN_CREDIBILITY = {
    # Reference / encyclopedia
    "wikipedia.org": 1.0,
    "britannica.com": 0.95,
    "wikidata.org": 0.9,
    # News (major)
    "reuters.com": 0.95,
    "apnews.com": 0.95,
    "bbc.com": 0.9,
    "npr.org": 0.9,
    "nytimes.com": 0.85,
    "wsj.com": 0.85,
    "theguardian.com": 0.85,
    "washingtonpost.com": 0.85,
    "bloomberg.com": 0.9,
    "ft.com": 0.9,
    # Tech / dev
    "stackoverflow.com": 0.95,
    "github.com": 0.9,
    "gitlab.com": 0.85,
    "developer.mozilla.org": 0.95,
    "docs.python.org": 0.95,
    "pytorch.org": 0.9,
    "tensorflow.org": 0.9,
    "huggingface.co": 0.85,
    # Science / medical
    "pubmed.ncbi.nlm.nih.gov": 1.0,
    "nih.gov": 0.95,
    "who.int": 0.95,
    "cdc.gov": 0.95,
    "nature.com": 0.9,
    "science.org": 0.9,
    "arxiv.org": 0.9,
    # Finance
    "finance.yahoo.com": 0.85,
    "marketwatch.com": 0.8,
    "investopedia.com": 0.8,
    # General reference
    "wikihow.com": 0.7,
    "howtogeek.com": 0.75,
    "pcmag.com": 0.75,
    "theverge.com": 0.75,
    "arstechnica.com": 0.8,
    "techcrunch.com": 0.75,
    "engadget.com": 0.7,
    # Government / official
    ".gov": 0.95,
    ".edu": 0.9,
}

# Query type detection patterns
QUERY_TYPE_PATTERNS = {
    "stock": re.compile(r"\b(stock|share|ticker|price|market cap|trading|nasdaq|nyse)\b", re.I),
    "weather": re.compile(r"\b(weather|temperature|forecast|humidity|rain|snow)\b", re.I),
    "definition": re.compile(r"^(what is|define|definition of|meaning of)\b", re.I),
    "howto": re.compile(r"^(how to|how do i|how can i|steps to|guide to|tutorial)\b", re.I),
    "comparison": re.compile(r"\b(vs|versus|compare|difference between|better|which is better)\b", re.I),
    "news": re.compile(r"\b(news|latest|recent|today|breaking|just in)\b", re.I),
    "code": re.compile(r"\b(code|function|class|api|library|package|npm|pip|import|error|exception|bug)\b", re.I),
    "product": re.compile(r"\b(buy|price|cost|review|best|top|recommend|alternative)\b", re.I),
    "fact": re.compile(r"^(who|what|when|where|why|which)\b", re.I),
}

# ──────────────────────────────────────────────────────────────────────────────
# Data Classes
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    source: str
    domain: str
    credibility: float = 0.5
    fetched_content: str = ""
    relevance_score: float = 0.0
    timestamp: float = field(default_factory=time.time)

@dataclass
class SearchResponse:
    query: str
    results: list[SearchResult]
    query_type: str
    structured_data: dict = field(default_factory=dict)
    cache_hit: bool = False
    search_time_ms: int = 0
    engines_used: list[str] = field(default_factory=list)

# ──────────────────────────────────────────────────────────────────────────────
# HTTP Session with Retry & Connection Pooling
# ──────────────────────────────────────────────────────────────────────────────

def _create_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=2,
        backoff_factor=0.3,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=20)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(DEFAULT_HEADERS)
    return session

_SESSION = _create_session()
_EXECUTOR = ThreadPoolExecutor(max_workers=4)

# ──────────────────────────────────────────────────────────────────────────────
# Cache
# ──────────────────────────────────────────────────────────────────────────────

_SEARCH_CACHE: dict[str, tuple[SearchResponse, float]] = {}

def _cache_key(query: str, engines: tuple[str, ...]) -> str:
    raw = f"{query.lower().strip()}|{','.join(sorted(engines))}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]

def _get_cached(query: str, engines: tuple[str, ...]) -> Optional[SearchResponse]:
    key = _cache_key(query, engines)
    if key in _SEARCH_CACHE:
        resp, ts = _SEARCH_CACHE[key]
        if time.time() - ts < CACHE_TTL_SECONDS:
            resp.cache_hit = True
            return resp
        else:
            del _SEARCH_CACHE[key]
    return None

def _set_cache(query: str, engines: tuple[str, ...], resp: SearchResponse) -> None:
    # Don't cache empty results
    if not resp.results:
        return
    key = _cache_key(query, engines)
    _SEARCH_CACHE[key] = (resp, time.time())
    # Simple cache size limit
    if len(_SEARCH_CACHE) > 200:
        oldest = min(_SEARCH_CACHE.items(), key=lambda x: x[1][1])[0]
        del _SEARCH_CACHE[oldest]

# ──────────────────────────────────────────────────────────────────────────────
# Query Analysis
# ──────────────────────────────────────────────────────────────────────────────

def detect_query_type(query: str) -> str:
    """Detect the type of query for specialized handling."""
    q = query.strip().lower()
    for qtype, pattern in QUERY_TYPE_PATTERNS.items():
        if pattern.search(q):
            return qtype
    return "general"

def extract_entities(query: str) -> dict:
    """Extract key entities from query for better search."""
    entities = {}
    # Stock tickers (3-5 uppercase letters)
    tickers = re.findall(r"\b([A-Z]{3,5})\b", query.upper())
    if tickers:
        entities["tickers"] = tickers
    # Numbers with units
    numbers = re.findall(r"\b(\d+(?:\.\d+)?)\s*(%|percent|dollars?|usd|eur|gbp|btc|eth)\b", query, re.I)
    if numbers:
        entities["numbers"] = numbers
    # Dates
    dates = re.findall(r"\b(20\d{2}|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b", query, re.I)
    if dates:
        entities["dates"] = dates
    return entities

# ──────────────────────────────────────────────────────────────────────────────
# Search Engine Parsers
# ──────────────────────────────────────────────────────────────────────────────

def _parse_ddgs_lib(query: str, max_results: int = 10, fast_mode: bool = False) -> list[SearchResult]:
    """Use DDGS library directly for reliable results."""
    results = []
    # Retry with backoff for DDGS rate limiting (skip in fast_mode)
    max_attempts = 1 if fast_mode else 3
    for attempt in range(max_attempts):
        try:
            from ddgs import DDGS
            with DDGS() as ddgs_client:
                ddg_results = list(ddgs_client.text(query, max_results=max_results + 4))
                for r in ddg_results:
                    href = (r.get("href") or "").lower()
                    title = (r.get("title") or "").strip()
                    body = (r.get("body") or "").strip()

                    if "wikipedia.org" in href or "wikipedia" in title.lower():
                        continue

                    if body and len(body) > 25:
                        snippet = f"{title}: {body}" if title else body
                        url = r.get("href", "")
                        if url:
                            domain = urllib.parse.urlparse(url).netloc.replace("www.", "")
                            results.append(SearchResult(
                                title=title, url=url, snippet=snippet,
                                source="ddgs", domain=domain,
                                credibility=_get_domain_credibility(domain)
                            ))
                    if len(results) >= max_results:
                        break
            # Success - break retry loop
            break
        except Exception as e:
            logger.debug(f"[DDGS Lib Error attempt {attempt+1}]: {e}")
            if attempt < max_attempts - 1:
                time.sleep(0.5 * (attempt + 1))  # 0.5s, 1s backoff
            else:
                logger.warning(f"[DDGS Lib] All retries failed for query: {query}")
    return results[:10]

def _parse_brave_html(html: str, query: str) -> list[SearchResult]:
    """Parse Brave Search HTML results."""
    results = []
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for result in soup.select(".snippet, .result-item"):
            title_elem = result.select_one("h3, .title, .result-title")
            snippet_elem = result.select_one(".snippet-text, .description, p")
            link_elem = result.select_one("a[href]")
            if title_elem and snippet_elem and link_elem:
                title = title_elem.get_text(strip=True)
                snippet = snippet_elem.get_text(strip=True)
                url = link_elem.get("href", "")
                if url and snippet and not url.startswith("/"):
                    domain = urllib.parse.urlparse(url).netloc.replace("www.", "")
                    results.append(SearchResult(
                        title=title, url=url, snippet=snippet,
                        source="brave", domain=domain,
                        credibility=_get_domain_credibility(domain)
                    ))
    except Exception as e:
        logger.debug(f"[Brave Parse Error]: {e}")
    return results[:10]

def _parse_searxng_json(json_text: str, query: str) -> list[SearchResult]:
    """Parse SearXNG JSON results."""
    results = []
    try:
        data = json.loads(json_text)
        for r in data.get("results", []):
            title = r.get("title", "").strip()
            snippet = r.get("content", "").strip()
            url = r.get("url", "").strip()
            if title and snippet and url:
                domain = urllib.parse.urlparse(url).netloc.replace("www.", "")
                results.append(SearchResult(
                    title=title, url=url, snippet=snippet,
                    source="searxng", domain=domain,
                    credibility=_get_domain_credibility(domain)
                ))
    except Exception as e:
        logger.debug(f"[SearXNG Parse Error]: {e}")
    return results[:10]

def _parse_bing_html(html: str, query: str) -> list[SearchResult]:
    """Parse Bing HTML results."""
    results = []
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for result in soup.select(".b_algo"):
            title_elem = result.select_one("h2 a")
            snippet_elem = result.select_one(".b_caption p, .b_snippet")
            if title_elem and snippet_elem:
                title = title_elem.get_text(strip=True)
                snippet = snippet_elem.get_text(strip=True)
                url = title_elem.get("href", "")
                # Handle Bing redirect URLs - extract real URL from 'u' parameter
                if url.startswith("https://www.bing.com/ck/a"):
                    # Extract real URL from redirect parameter
                    parsed = urllib.parse.urlparse(url)
                    params = urllib.parse.parse_qs(parsed.query)
                    if 'u' in params:
                        url = params['u'][0]
                    else:
                        continue
                if url and snippet and not url.startswith("https://www.bing.com/ck"):
                    domain = urllib.parse.urlparse(url).netloc.replace("www.", "")
                    results.append(SearchResult(
                        title=title, url=url, snippet=snippet,
                        source="bing", domain=domain,
                        credibility=_get_domain_credibility(domain)
                    ))
    except Exception as e:
        logger.debug(f"[Bing Parse Error]: {e}")
    return results[:10]

PARSERS = {
    "ddgs_lib": _parse_ddgs_lib,
    "searxng_json": _parse_searxng_json,
    "bing_html": _parse_bing_html,
}

def _get_domain_credibility(domain: str) -> float:
    """Get credibility score for a domain."""
    domain = domain.lower()
    for known, score in DOMAIN_CREDIBILITY.items():
        if known.startswith("."):
            if domain.endswith(known):
                return score
        elif known in domain:
            return score
    return 0.5

# ──────────────────────────────────────────────────────────────────────────────
# Content Fetching & Extraction
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_page_content(url: str) -> str:
    """Fetch and extract main content from a page."""
    try:
        resp = _SESSION.get(url, timeout=FETCH_TIMEOUT, allow_redirects=True)
        resp.raise_for_status()
        html = resp.text
        
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        
        # Remove noise
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "iframe", "noscript", "svg", "form", "button", "input"]):
            tag.decompose()
        
        # Try to find main content
        main = soup.select_one("main, article, .content, .post, .entry, #content, .main-content, .article-body")
        if main:
            text = main.get_text(separator=" ", strip=True)
        else:
            # Fallback: get all paragraph text
            paragraphs = soup.select("p")
            text = " ".join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 50)
        
        # Clean up
        text = re.sub(r"\s+", " ", text).strip()
        return text[:MAX_CONTENT_LENGTH]
    except Exception as e:
        logger.debug(f"[Fetch Error {url}]: {e}")
        return ""

def _fetch_contents_parallel(results: list[SearchResult]) -> list[SearchResult]:
    """Fetch full content for top results in parallel."""
    top_results = results[:TOP_K_RESULTS_TO_FETCH]
    urls = [r.url for r in top_results]
    
    def fetch_one(url):
        return _fetch_page_content(url)
    
    contents = list(_EXECUTOR.map(fetch_one, urls))
    
    for result, content in zip(top_results, contents):
        result.fetched_content = content
    
    return results

# ──────────────────────────────────────────────────────────────────────────────
# Relevance Scoring (Embedding-based if available, otherwise heuristic)
# ──────────────────────────────────────────────────────────────────────────────

def _score_relevance(query: str, result: SearchResult, query_type: str) -> float:
    """Score result relevance to query."""
    score = 0.0
    q_lower = query.lower()
    q_words = set(q_lower.split())
    
    # Title match
    title_words = set(result.title.lower().split())
    title_overlap = len(q_words & title_words) / max(len(q_words), 1)
    score += title_overlap * 0.4
    
    # Snippet match
    snippet_words = set(result.snippet.lower().split())
    snippet_overlap = len(q_words & snippet_words) / max(len(q_words), 1)
    score += snippet_overlap * 0.3
    
    # Domain credibility
    score += result.credibility * 0.2
    
    # Fetched content match (if available)
    if result.fetched_content:
        content_words = set(result.fetched_content.lower().split())
        content_overlap = len(q_words & content_words) / max(len(q_words), 1)
        score += content_overlap * 0.1
    
    # Query-type specific boosts
    if query_type == "news" and "news" in result.domain:
        score += 0.15
    elif query_type == "code" and any(d in result.domain for d in ["stackoverflow", "github", "docs.", "developer."]):
        score += 0.15
    elif query_type == "stock" and "finance" in result.domain:
        score += 0.15
    elif query_type == "definition" and "wikipedia" in result.domain:
        score += 0.1
    
    # Recency boost (newer content slightly preferred)
    age_hours = (time.time() - result.timestamp) / 3600
    if age_hours < 24:
        score += 0.05 * (1 - age_hours / 24)
    
    return min(score, 1.0)

# ──────────────────────────────────────────────────────────────────────────────
# Structured Extraction for Specific Query Types
# ──────────────────────────────────────────────────────────────────────────────

def _extract_structured_data(query: str, results: list[SearchResult], query_type: str) -> dict:
    """Extract structured data based on query type."""
    structured = {"type": query_type}
    
    if query_type == "stock":
        # Try to extract stock price from results
        for r in results:
            text = f"{r.title} {r.snippet} {r.fetched_content}"
            # Look for price patterns
            price_match = re.search(r"\$?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)\s*(?:USD|per share|/share)?", text)
            if price_match:
                structured["price"] = price_match.group(1)
                break
    
    elif query_type == "weather":
        for r in results:
            text = f"{r.title} {r.snippet} {r.fetched_content}"
            temp_match = re.search(r"(\d{1,3})\s*[°FfCc]", text)
            if temp_match:
                structured["temperature"] = temp_match.group(1)
                break
    
    elif query_type == "definition":
        # Use first result's snippet as definition
        if results:
            structured["definition"] = results[0].snippet[:500]
            structured["source"] = results[0].url
    
    elif query_type == "howto":
        # Extract steps from content
        steps = []
        for r in results:
            if r.fetched_content:
                # Look for numbered steps
                step_matches = re.findall(r"(?:^|\n)\s*\d+[\.\)]\s*(.+?)(?=\n\s*\d+[\.\)]|\n\n|$)", r.fetched_content)
                steps.extend(step_matches[:5])
        if steps:
            structured["steps"] = steps[:8]
    
    return structured

# ──────────────────────────────────────────────────────────────────────────────
# Main Search Function
# ──────────────────────────────────────────────────────────────────────────────

def search_web(
    query: str,
    max_results: int = TOP_K_FINAL_SNIPPETS,
    engines: Optional[list[str]] = None,
    fetch_content: bool = False,  # Default to False for speed
    use_cache: bool = True,
    fast_mode: bool = False,  # New: ultra-fast mode for voice
) -> SearchResponse:
    """
    Beast-mode web search.
    
    Args:
        query: Search query
        max_results: Number of final snippets to return
        engines: Which engines to use (default: all)
        fetch_content: Whether to fetch full page content for top results
        use_cache: Whether to use cached results
        fast_mode: If True, use only DDGS engine, skip content fetching, minimal processing
    
    Returns:
        SearchResponse with ranked results, structured data, and metadata
    """
    start_time = time.time()
    query = query.strip()
    if not query:
        return SearchResponse(query="", results=[], query_type="empty")
    
    # Fast mode overrides
    if fast_mode:
        engines = ["bing"]  # Use Bing HTML (reliable, no rate limits)
        fetch_content = False
        max_results = min(max_results, 3)
    
    engines = engines or list(SEARCH_ENGINES.keys())
    engines_tuple = tuple(sorted(engines))
    query_type = detect_query_type(query)
    
    # Check cache
    if use_cache:
        cached = _get_cached(query, engines_tuple)
        if cached:
            logger.info(f"[WebSearch] Cache hit for: {query[:50]}")
            return cached
    
    # Search all engines in parallel
    all_results = []
    engines_used = []
    
    def search_engine(engine_name: str) -> list[SearchResult]:
        engine = SEARCH_ENGINES[engine_name]
        try:
            if engine["method"] == "LIBRARY":
                # Direct library call (e.g., DDGS)
                parser = PARSERS[engine["parser"]]
                # Pass fast_mode to DDGS parser
                if engine["parser"] == "ddgs_lib":
                    results = parser(query, max_results=10, fast_mode=fast_mode)
                else:
                    results = parser(query, max_results=10)
            else:
                url = engine["url"].format(query=urllib.parse.quote(query))
                if engine["method"] == "POST":
                    resp = _SESSION.post(url, data=engine.get("params", {}), timeout=REQUEST_TIMEOUT)
                else:
                    resp = _SESSION.get(url, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
                
                parser = PARSERS[engine["parser"]]
                results = parser(resp.text, query)
            
            # Apply engine weight
            for r in results:
                r.relevance_score *= engine["weight"]
            
            return results
        except Exception as e:
            logger.debug(f"[{engine_name} Search Error]: {e}")
            return []
    
    # Parallel engine searches
    futures = {_EXECUTOR.submit(search_engine, e): e for e in engines}
    for future in futures:
        engine_name = futures[future]
        try:
            results = future.result(timeout=REQUEST_TIMEOUT + 2)
            if results:
                all_results.extend(results)
                engines_used.append(engine_name)
        except Exception as e:
            logger.debug(f"[{engine_name} Future Error]: {e}")
    
    # Deduplicate by URL
    seen_urls = set()
    unique_results = []
    for r in all_results:
        if r.url not in seen_urls:
            seen_urls.add(r.url)
            unique_results.append(r)
    
    # Fetch full content for top results (skip in fast_mode)
    if fetch_content and unique_results and not fast_mode:
        unique_results = _fetch_contents_parallel(unique_results)
    
    # Score relevance
    for r in unique_results:
        r.relevance_score = _score_relevance(query, r, query_type)
    
    # Sort by relevance
    unique_results.sort(key=lambda x: x.relevance_score, reverse=True)
    
    # Take top results
    final_results = unique_results[:max_results]
    
    # Extract structured data (skip in fast_mode for speed)
    structured_data = {}
    if not fast_mode:
        structured_data = _extract_structured_data(query, final_results, query_type)
    
    # Build response
    response = SearchResponse(
        query=query,
        results=final_results,
        query_type=query_type,
        structured_data=structured_data,
        search_time_ms=int((time.time() - start_time) * 1000),
        engines_used=engines_used,
    )
    
    # Cache
    if use_cache:
        _set_cache(query, engines_tuple, response)
    
    logger.info(f"[WebSearch] '{query[:50]}' → {len(final_results)} results in {response.search_time_ms}ms (engines: {engines_used})")
    return response

# ──────────────────────────────────────────────────────────────────────────────
# Formatting for LLM Context
# ──────────────────────────────────────────────────────────────────────────────

def format_for_llm(response: SearchResponse, max_chars: int = 4000) -> str:
    """Format search response for LLM context injection."""
    if not response.results:
        return "No search results found."
    
    parts = [f"Web Search Results for: '{response.query}' (Query type: {response.query_type})"]
    
    if response.structured_data:
        parts.append(f"Structured Data: {json.dumps(response.structured_data, ensure_ascii=False)}")
    
    parts.append("\nSources:")
    for i, r in enumerate(response.results, 1):
        credibility_indicator = "★" if r.credibility > 0.8 else "☆" if r.credibility > 0.6 else ""
        content_preview = ""
        if r.fetched_content:
            content_preview = f"\n  Full content preview: {r.fetched_content[:500]}..."
        parts.append(
            f"{i}. {credibility_indicator} {r.title}\n"
            f"   URL: {r.url}\n"
            f"   Snippet: {r.snippet}{content_preview}"
        )
    
    result = "\n".join(parts)
    return result[:max_chars]

# ──────────────────────────────────────────────────────────────────────────────
# Drop-in Replacement for scrape_web_info
# ──────────────────────────────────────────────────────────────────────────────

def scrape_web_info(query: str, max_results: int = 4) -> str:
    """
    Drop-in replacement for Searchnow.scrape_web_info.
    Returns formatted string for backward compatibility.
    """
    response = search_web(query, max_results=max_results)
    if not response.results:
        return ""
    
    # Return pipe-separated snippets for backward compat
    snippets = []
    for r in response.results:
        snippet = f"{r.title}: {r.snippet}"
        if r.fetched_content:
            snippet += f" | {r.fetched_content[:300]}"
        snippets.append(snippet)
    
    return " | ".join(snippets)[:3000]

# ──────────────────────────────────────────────────────────────────────────────
# YouTube Resolution & Google Search Utilities (from original Searchnow.py)
# ──────────────────────────────────────────────────────────────────────────────

# Pre-compiled regex patterns for YouTube and query cleaning
_RE_SEARCH_PREFIX = re.compile(
    r"^(?:(?:hey\s+|hi\s+|hello\s+)?amigo\s*)?"
    r"(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?"
    r"(?:tell\s+me\s+(?:more\s+about|about)?|"
    r"what\s+(?:can\s+you\s+tell\s+me\s+about|do\s+you\s+know\s+about|is|are|was|were)|"
    r"who\s+(?:is|are|was|were)|"
    r"when\s+(?:is|was|did)|"
    r"where\s+(?:is|are|was)|"
    r"search\s+(?:google\s+for|the\s+web\s+for|online\s+for|for)?|"
    r"look\s+up|google|find|show\s+me|give\s+me\s+(?:info|information)\s+(?:on|about))\s*",
    re.IGNORECASE,
)
_RE_SEARCH_SUFFIX = re.compile(r"\s+(?:please|for\s+me|right\s+now|now|today|online|on\s+the\s+web|on\s+google)$", re.IGNORECASE)
_RE_STOCK_CLEAN = re.compile(r"\b(stock|stocks|price|prices|share|shares|quote|quotes|trading|today|current|live|latest|value|how much is|can you tell me|tell me|what is|what's|the|of|at)\b", re.IGNORECASE)
_RE_GOOGLE_CLEAN = re.compile(r"^(show me|open|search|find|look up|what is|google)\s+", re.IGNORECASE)
_RE_YT_PREFIX = re.compile(r"^(?:(?:hey\s+|hi\s+|hello\s+)?amigo\s*)?(?:please\s+|can\s+(?:you|u)\s+|could\s+(?:you|u)\s+)?(?:open youtube|play|listen to|put on|stream|watch|search youtube for)?\s*", re.IGNORECASE)
_RE_YT_SUFFIX = re.compile(r"\s+(?:on youtube|in youtube|youtube|for me|please|now|right now)$", re.IGNORECASE)
_RE_YT_LATEST = re.compile(r"\b(latest|newest|recent|new|today|yesterday)\b", re.IGNORECASE)
_RE_YT_LATEST_CLEAN = re.compile(r"['’]?s?\s*\b(latest|newest|recent|new|today|yesterday|videos?|uploads?|vids?)\b", re.IGNORECASE)
_RE_YT_LIVE = re.compile(r"\b(live\s*stream|livestream|currently\s+live|current\s+live|live\s+now|is\s+live|live)\b", re.IGNORECASE)
_RE_YT_LIVE_CLEAN = re.compile(r"\b(current\s+live\s+stream|currently\s+live|current\s+live|live\s*stream|livestream|live\s+now|is\s+live|live)\b", re.IGNORECASE)
_RE_YT_NOISE = re.compile(r"\b(that\s+(?:does|do|makes?|creates?)|who\s+(?:does|do|makes?|creates?))\b", re.IGNORECASE)
_RE_WHITESPACE = re.compile(r"\s+")
_RE_VID_ID = re.compile(r'"videoRenderer":\{"videoId":"([a-zA-Z0-9_-]{11})"')
_RE_TITLE = re.compile(r'"title":\{"runs":\[\{"text":"([^"]+)"')
_RE_CHANNEL = re.compile(r'"ownerText":\{"runs":\[\{"text":"([^"]+)"')


def clean_search_query(raw_query: str) -> str:
    """Extract core search terms by stripping conversational prefixes and suffixes."""
    if not raw_query:
        return ""
    q = raw_query.strip()
    q = _RE_SEARCH_PREFIX.sub("", q).strip()
    q = _RE_SEARCH_SUFFIX.sub("", q).strip()
    return q if len(q) >= 2 else raw_query.strip()


def searchGoogle(query: str) -> None:
    """Open Google search in the default web browser."""
    if not query:
        return
    clean_q = _RE_GOOGLE_CLEAN.sub("", query).strip()
    clean_q = clean_q.replace("google", "").strip() or query
    webbrowser.open(f"https://www.google.com/search?q={urllib.parse.quote(clean_q)}")


def _fetch_top_yt_video(search_url: str) -> Optional[dict]:
    """Helper to query YouTube and extract top video ID, title, channel, and live status."""
    try:
        req = urllib.request.Request(search_url, headers=DEFAULT_HEADERS)
        with urllib.request.urlopen(req, timeout=4.5) as resp:
            page_html = resp.read().decode("utf-8", errors="ignore")

        # 1. Parse ytInitialData JSON for highest accuracy & live badge detection
        m = re.search(r"var ytInitialData\s*=\s*({.+?});</script>", page_html)
        if m:
            try:
                data = json.loads(m.group(1))
                contents = data.get("contents", {}).get("twoColumnSearchResultsRenderer", {}).get("primaryContents", {}).get("sectionListRenderer", {}).get("contents", [])
                for sec in contents:
                    for item in sec.get("itemSectionRenderer", {}).get("contents", []):
                        if "videoRenderer" in item:
                            vr = item["videoRenderer"]
                            vid_id = vr.get("videoId")
                            if vid_id:
                                title = vr.get("title", {}).get("runs", [{}])[0].get("text", "")
                                channel = vr.get("ownerText", {}).get("runs", [{}])[0].get("text", "YouTube")
                                badges = [b.get("metadataBadgeRenderer", {}).get("label") for b in vr.get("badges", [])]
                                is_live = any("LIVE" in str(b).upper() for b in badges) or "BADGE_STYLE_TYPE_LIVE_NOW" in str(vr.get("badges"))
                                return {
                                    "video_id": vid_id,
                                    "title": title,
                                    "channel": channel,
                                    "url": f"https://www.youtube.com/watch?v={vid_id}&autoplay=1",
                                    "is_live": is_live,
                                }
            except Exception:
                pass

        # 2. Regex fallback
        vid_m = _RE_VID_ID.search(page_html)
        if not vid_m:
            return None

        vid_id = vid_m.group(1)
        title_m = _RE_TITLE.search(page_html)
        title = title_m.group(1).replace(r"\u0026", "&").replace(r'\"', '"') if title_m else ""
        channel_m = _RE_CHANNEL.search(page_html)
        channel = channel_m.group(1).replace(r"\u0026", "&") if channel_m else "YouTube"
        is_live = "BADGE_STYLE_TYPE_LIVE_NOW" in page_html

        return {
            "video_id": vid_id,
            "title": title,
            "channel": channel,
            "url": f"https://www.youtube.com/watch?v={vid_id}&autoplay=1",
            "is_live": is_live,
        }
    except Exception:
        return None


def resolve_youtube_video(query: str) -> dict:
    """Extract query, fetch top YouTube video, title, and channel, with support for live streams and fallbacks."""
    q = (query or "").strip()
    # 1. Clean conversational prefixes and suffixes
    q = _RE_YT_PREFIX.sub("", q)
    q = _RE_YT_SUFFIX.sub("", q).strip()

    is_live = bool(_RE_YT_LIVE.search(q))
    is_latest = bool(_RE_YT_LATEST.search(q))

    clean_q = q
    if is_live:
        clean_q = _RE_YT_LIVE_CLEAN.sub("", clean_q).strip()
    if is_latest:
        clean_q = _RE_YT_LATEST_CLEAN.sub("", clean_q).strip()
    clean_q = _RE_YT_NOISE.sub("", clean_q).strip()
    clean_q = _RE_WHITESPACE.sub(" ", clean_q).strip() or q

    result = {
        "url": f"https://www.youtube.com/results?search_query={urllib.parse.quote(clean_q)}",
        "title": clean_q.title(),
        "channel": "YouTube",
        "video_id": "",
        "query": clean_q,
        "is_live": False,
        "not_live_fallback": False,
    }

    # 1. If live stream requested:
    # First search clean_q + " live" which YouTube's search ranks best for live broadcasts
    if is_live:
        live_kw_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(clean_q + ' live')}"
        hit = _fetch_top_yt_video(live_kw_url)
        if hit and hit.get("is_live"):
            result.update(hit)
            return result

        # Also try YouTube Live filter (sp=EgJAAQ%253D%253D)
        live_sp_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(clean_q)}&sp=EgJAAQ%253D%253D"
        hit_sp = _fetch_top_yt_video(live_sp_url)
        if hit_sp:
            result.update(hit_sp)
            result["is_live"] = True
            return result

        # If not currently live, fall back to top result from live keyword search or latest
        if hit:
            result.update(hit)
            result["not_live_fallback"] = True
            return result

        latest_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(clean_q + ' stream')}&sp=CAISAhAB"
        hit_latest = _fetch_top_yt_video(latest_url)
        if hit_latest:
            result.update(hit_latest)
            result["not_live_fallback"] = True
            return result

    # 2. If latest upload requested, search with Upload Date filter (sp=CAISAhAB)
    if is_latest:
        latest_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(clean_q)}&sp=CAISAhAB"
        hit = _fetch_top_yt_video(latest_url)
        if hit:
            result.update(hit)
            return result

    # 3. Standard relevance search (sp=EgIQAQ%253D%253D)
    standard_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(clean_q)}&sp=EgIQAQ%253D%253D"
    hit = _fetch_top_yt_video(standard_url)
    if hit:
        result.update(hit)
        return result

    return result


def searchYoutube(query: str) -> str:
    """Play YouTube video or search YouTube in browser."""
    media_info = resolve_youtube_video(query)
    webbrowser.open(media_info["url"])
    return media_info["url"]


# ──────────────────────────────────────────────────────────────────────────────
# Async Version for UI Server
# ──────────────────────────────────────────────────────────────────────────────

async def async_search_web(query: str, **kwargs) -> SearchResponse:
    """Async wrapper for use in async contexts."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_EXECUTOR, lambda: search_web(query, **kwargs))

# ──────────────────────────────────────────────────────────────────────────────
# Cleanup
# ──────────────────────────────────────────────────────────────────────────────

def shutdown():
    """Shutdown executor and clear cache."""
    _EXECUTOR.shutdown(wait=True)
    _SEARCH_CACHE.clear()