"""
web_search.py - Autonomous Deep-Retrieval, Passage-Ranking & Federated Search Engine for Amigo.

Architecture (Zero Hardcoded Topic/Domain Rules):
  * Information-Theoretic Query Decomposer: Automatically generates a parallel query matrix
    (Natural Question + High-Specificity Token Skeleton) using token length entropy and casing
    signals—no domain regexes or hardcoded entity lists.
  * Multi-Index Federated Race: Simultaneously races General Web (DDGS, Bing, Tavily, Brave, SearXNG),
    Live News (Google News RSS, DDGS News), and Video Scene Metadata (YouTube InnerTube) in parallel.
  * Hybrid T-RRF + BM25 Proximity Reranking: Scores results using multi-engine Reciprocal Rank Fusion
    combined with Okapi BM25 term saturation, bigram proximity bonuses, and exponential recency decay.
  * Query-Focused Sliding-Window Passage Extractor: Fetches top candidate pages in parallel, strips
    boilerplate + extracts JSON-LD schema data, and slices the exact paragraphs with the highest
    query-token density rather than blindly taking the top of the page.
"""

from __future__ import annotations

import asyncio
import base64
import email.utils
import html as _html
import json
import logging
import math
import os
import re
import threading
import time
import urllib.parse
import webbrowser
import xml.etree.ElementTree as ET
from collections import Counter, OrderedDict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Callable, Optional

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    import requests
    _HAS_HTTPX = False

logger = logging.getLogger("amigo.web_search")

# ──────────────────────────────────────────────────────────────────────────────
# Dynamic Configuration (Environment-Driven, No Hardcoded Assumptions)
# ──────────────────────────────────────────────────────────────────────────────

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

SEARCH_REGION = os.getenv("AMIGO_SEARCH_REGION", "us-en")
SEARXNG_URL = os.getenv("AMIGO_SEARXNG_URL", "").rstrip("/")
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
YT_REGION = os.getenv("AMIGO_YT_REGION", "")

TOP_K_FINAL_SNIPPETS = 6
PER_ENGINE_FULL = 8
PER_ENGINE_FAST = 6
SNIPPET_CHARS = 520

# Fast, non-blocking budgets (seconds)
_BUDGETS = {"fast": 2.5, "full": 3.8}
MERGE_GRACE_S = 0.45
MERGE_GRACE_FAST_S = 0.35
HTTP_TIMEOUT = 2.8

FETCH_TIMEOUT = 2.4
FETCH_DEADLINE_S = 2.8
TOP_K_RESULTS_TO_FETCH = 4
MAX_PAGE_BYTES = 450_000
MAX_CONTENT_CHARS = 1800

CACHE_MAX = 256
CACHE_TTL_S = 300
YT_CACHE_TTL_S = 6 * 3600
YT_BUDGET_S = 6.0

RRF_K = 60

# Grammatical function words (strictly syntactic, zero domain assumptions)
_SYNTAX_FUNCTION_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "if", "while", "of", "at", "by", "for",
    "with", "about", "into", "through", "during", "to", "from", "in", "on", "is",
    "are", "was", "were", "be", "been", "being", "have", "has", "had", "do", "does",
    "did", "will", "would", "shall", "should", "can", "could", "may", "might",
    "what", "which", "who", "whom", "whose", "when", "where", "why", "how", "that",
    "this", "these", "those", "it", "its", "they", "them", "their", "there",
})

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
    published: str = ""
    age_days: Optional[float] = None
    score: float = 0.0
    fetched_content: str = ""

    @property
    def relevance_score(self) -> float:
        return self.score

    @property
    def credibility(self) -> float:
        return 0.9 if self.domain.endswith((".gov", ".edu")) else 0.78


@dataclass
class SearchResponse:
    query: str
    results: list[SearchResult]
    query_type: str = "federated"
    structured_data: dict = field(default_factory=dict)
    cache_hit: bool = False
    search_time_ms: int = 0
    engines_used: list[str] = field(default_factory=list)


@dataclass
class YTVideo:
    video_id: str
    title: str = ""
    channel: str = "YouTube"
    is_live: bool = False
    duration_s: Optional[int] = None
    source: str = ""


# ──────────────────────────────────────────────────────────────────────────────
# HTTP/2 Client Pool, Executor, Cache & Adaptive Circuit Breaker
# ──────────────────────────────────────────────────────────────────────────────


class _HttpClient:
    """Unified HTTP/2 client using httpx with automatic fallback to requests."""

    def __init__(self):
        if _HAS_HTTPX:
            self._client = httpx.Client(
                http2=True,
                headers=DEFAULT_HEADERS,
                timeout=httpx.Timeout(HTTP_TIMEOUT, connect=1.2),
                follow_redirects=True,
                limits=httpx.Limits(max_keepalive_connections=24, max_connections=48),
            )
        else:
            from requests.adapters import HTTPAdapter
            self._client = requests.Session()
            adapter = HTTPAdapter(max_retries=0, pool_connections=24, pool_maxsize=48)
            self._client.mount("http://", adapter)
            self._client.mount("https://", adapter)
            self._client.headers.update(DEFAULT_HEADERS)

    def get(
        self,
        url: str,
        params: dict | None = None,
        headers: dict | None = None,
        cookies: dict | None = None,
        timeout: float = HTTP_TIMEOUT,
    ):
        if _HAS_HTTPX:
            resp = self._client.get(url, params=params, headers=headers, cookies=cookies, timeout=timeout)
            resp.raise_for_status()
            return resp
        resp = self._client.get(url, params=params, headers=headers, cookies=cookies, timeout=(1.2, timeout))
        resp.raise_for_status()
        return resp

    def post(
        self,
        url: str,
        json_body: dict,
        headers: dict | None = None,
        cookies: dict | None = None,
        timeout: float = HTTP_TIMEOUT,
    ):
        if _HAS_HTTPX:
            resp = self._client.post(url, json=json_body, headers=headers, cookies=cookies, timeout=timeout)
            resp.raise_for_status()
            return resp
        resp = self._client.post(url, json=json_body, headers=headers, cookies=cookies, timeout=(1.2, timeout))
        resp.raise_for_status()
        return resp


_HTTP = _HttpClient()
_EXECUTOR = ThreadPoolExecutor(max_workers=16, thread_name_prefix="amigo-search")


class _TTLCache:
    def __init__(self, maxsize: int):
        self._d: OrderedDict = OrderedDict()
        self._max = maxsize
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            item = self._d.get(key)
            if item is None:
                return None
            value, expires = item
            if expires < time.time():
                del self._d[key]
                return None
            self._d.move_to_end(key)
            return value

    def set(self, key, value, ttl: float) -> None:
        with self._lock:
            self._d[key] = (value, time.time() + ttl)
            self._d.move_to_end(key)
            while len(self._d) > self._max:
                self._d.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()


class _Health:
    """Adaptive circuit breaker: temporarily sidelines failing providers so dead engines cost 0ms."""

    def __init__(self):
        self._lock = threading.Lock()
        self._fails: dict[str, int] = {}
        self._skip_until: dict[str, float] = {}

    def available(self, name: str) -> bool:
        with self._lock:
            return time.time() >= self._skip_until.get(name, 0.0)

    def ok(self, name: str) -> None:
        with self._lock:
            self._fails.pop(name, None)
            self._skip_until.pop(name, None)

    def fail(self, name: str, permanent: bool = False) -> None:
        with self._lock:
            n = self._fails.get(name, 0) + 1
            self._fails[name] = n
            if permanent:
                self._skip_until[name] = float("inf")
            elif n >= 2:
                self._skip_until[name] = time.time() + min(180.0, 10.0 * (2 ** (n - 1)))


_HEALTH = _Health()
_SEARCH_CACHE = _TTLCache(CACHE_MAX)
_YT_CACHE = _TTLCache(128)


def _select_healthy(names: list[str]) -> list[str]:
    healthy = [n for n in names if _HEALTH.available(n)]
    return healthy or list(names)


# ──────────────────────────────────────────────────────────────────────────────
# YouTube Constants & Parsing Primitives (Declared early for federated access)
# ──────────────────────────────────────────────────────────────────────────────

_yt_client_version = os.getenv("AMIGO_YT_CLIENT_VERSION", "2.20250901.01.00")
_YT_SEARCH_URL = "https://www.youtube.com/youtubei/v1/search?prettyPrint=false"
_YT_COOKIES = {"CONSENT": "YES+1", "SOCS": "CAI"}
_YT_P_VIDEOS = "EgIQAQ%3D%3D"
_YT_P_LIVE = "EgJAAQ%3D%3D"
_YT_P_NEWEST = "CAISAhAB"


def _runs_text(node) -> str:
    if not isinstance(node, dict):
        return ""
    if "simpleText" in node:
        return str(node["simpleText"])
    return "".join(r.get("text", "") for r in node.get("runs", []) if isinstance(r, dict))


def _parse_duration(text: str) -> Optional[int]:
    parts = (text or "").strip().split(":")
    if not parts or not all(p.isdigit() for p in parts):
        return None
    secs = 0
    for p in parts:
        secs = secs * 60 + int(p)
    return secs


def _walk_video_renderers(node):
    if isinstance(node, dict):
        vr = node.get("videoRenderer")
        if isinstance(vr, dict) and vr.get("videoId"):
            yield vr
        for v in node.values():
            yield from _walk_video_renderers(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_video_renderers(v)


def _videos_from_json(data, source: str) -> list[YTVideo]:
    out, seen = [], set()
    for vr in _walk_video_renderers(data):
        vid = vr["videoId"]
        if vid in seen:
            continue
        seen.add(vid)
        badges = [b.get("metadataBadgeRenderer", {}) for b in vr.get("badges", []) if isinstance(b, dict)]
        overlays = [
            o.get("thumbnailOverlayTimeStatusRenderer", {})
            for o in vr.get("thumbnailOverlays", [])
            if isinstance(o, dict)
        ]
        is_live = (
            any(b.get("style") == "BADGE_STYLE_TYPE_LIVE_NOW" for b in badges)
            or any(o.get("style") == "LIVE" for o in overlays)
        )
        channel = _runs_text(vr.get("ownerText")) or _runs_text(vr.get("longBylineText")) or "YouTube"
        out.append(YTVideo(
            video_id=vid,
            title=_runs_text(vr.get("title")),
            channel=channel,
            is_live=is_live,
            duration_s=_parse_duration(_runs_text(vr.get("lengthText"))),
            source=source,
        ))
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Text, Date, URL & Information-Theoretic Query Decomposition
# ──────────────────────────────────────────────────────────────────────────────

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_TRACKING_PARAM = re.compile(r"^(?:utm_|fbclid$|gclid$|ref$|ref_src$|igshid$)", re.I)
_WORD_RE = re.compile(r"[a-zA-Z0-9]+(?:-[a-zA-Z0-9]+)*")


def _clean_text(text: str) -> str:
    return _WS_RE.sub(" ", _html.unescape(_TAG_RE.sub(" ", text or ""))).strip()


def _clip(text: str, n: int) -> str:
    text = _clean_text(text)
    if len(text) <= n:
        return text
    return text[:n].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"


def _domain(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _norm_url(url: str) -> str:
    p = urllib.parse.urlparse(url)
    q = urllib.parse.urlencode(
        [(k, v) for k, v in urllib.parse.parse_qsl(p.query) if not _TRACKING_PARAM.match(k)]
    )
    return f"{_domain(url)}{p.path.rstrip('/')}" + (f"?{q}" if q else "")


def _parse_date_age(raw_date: str) -> tuple[str, Optional[float]]:
    if not raw_date:
        return "", None
    raw = raw_date.strip()
    dt = None
    try:
        dt = email.utils.parsedate_to_datetime(raw)
    except Exception:
        pass
    if dt is None:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            pass
    if dt is not None:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        age_days = max(0.0, (now - dt).total_seconds() / 86400.0)
        return dt.strftime("%Y-%m-%d"), age_days
    return raw[:10], None


def _mk(
    title: str,
    url: str,
    snippet: str,
    source: str,
    published: str = "",
    domain_override: str = "",
) -> SearchResult:
    title = _clean_text(title)
    pub_str, age_days = _parse_date_age(published)
    dom = domain_override or _domain(url)
    return SearchResult(
        title=title,
        url=url.strip(),
        snippet=_clip(snippet or title, SNIPPET_CHARS),
        source=source,
        domain=dom,
        published=pub_str,
        age_days=age_days,
    )


# Conversational wrapper stripper (preserves core nouns and entity names)
_LEAD_IN = (
    r"(?:(?:(?:hey|hi|hello|ok|okay)[\s,]+)?amigo\b[\s,]*"
    r"|(?:can|could|would|will)\s+(?:you|u)\s+"
    r"|please[\s,]+)"
)
_SEARCH_PHRASE = re.compile(
    r"^(?:" + _LEAD_IN + r")*"
    r"(?:search\s+(?:(?:google|the\s+web|the\s+internet|online)\s+)?for|look\s*up|find\s+out(?:\s+about)?)\s+",
    re.I,
)
_SEARCH_BARE_VERB = re.compile(r"^(?:" + _LEAD_IN + r")+(?:search|google|find)\s+", re.I)
_SEARCH_TRAIL = re.compile(r"\s+(?:on\s+google|on\s+the\s+(?:web|internet))$", re.I)
_SEARCH_INQUIRY = re.compile(
    r"^(?:" + _LEAD_IN + r")*"
    r"(?:(?:can|could|would|will)\s+(?:you|u)\s+(?:please\s+)?(?:tell\s+me|find|search|look\s*up|show\s+me|name)\b(?:\s+(?:the|what|which|about))?"
    r"|(?:tell\s+me|show\s+me|what\s+is\s+the\s+name\s+of|do\s+you\s+know\s+(?:the\s+name\s+of\s+)?)\b"
    r")\s*",
    re.I,
)
_SEARCH_INQUIRY_TRAIL = re.compile(
    r"\s*[,.]?\s*(?:i\s+want\s+to\s+know|can\s+you\s+tell\s+me|please)[.?!,\s]*$",
    re.I,
)


def clean_search_query(raw_query: str) -> str:
    """Strips voice wake words and search command wrappers while preserving full semantic intent."""
    if not raw_query:
        return ""
    q = raw_query.strip()
    q = _SEARCH_PHRASE.sub("", q)
    q = _SEARCH_BARE_VERB.sub("", q)
    q = _SEARCH_INQUIRY.sub("", q)
    q = _SEARCH_TRAIL.sub("", q)
    q = _SEARCH_INQUIRY_TRAIL.sub("", q).strip()
    return q if len(q) >= 2 else raw_query.strip()


def _token_specificity(token: str) -> float:
    """
    Information-theoretic token weight without any hardcoded vocabulary lists.
    Uses character length entropy, digit/hyphen signals, and capitalization.
    """
    low = token.lower()
    if low in _SYNTAX_FUNCTION_WORDS:
        return 0.05
    weight = 1.0 + 0.35 * math.log1p(len(low))
    if any(ch.isdigit() for ch in token):
        weight += 0.85
    if "-" in token:
        weight += 0.65
    if any(ch.isupper() for ch in token[1:]) or (token[0].isupper() and len(token) > 2):
        weight += 0.30
    return weight


def _build_query_variants(raw_query: str) -> list[str]:
    """
    Decomposes complex multi-clause queries into up to 2 complementary search queries:
      1. Clean natural-language query (hits Q&A forums, Reddit, StackExchange, articles)
      2. High-specificity content-word skeleton (strips syntactic glue so search engines don't
         drop critical tail entities when clauses are long).
    """
    clean_q = clean_search_query(raw_query) or raw_query.strip()
    words = _WORD_RE.findall(clean_q)
    if len(words) <= 5:
        return [clean_q]

    # Build a dense content-bearing skeleton preserving original sequential order
    skeleton_tokens = [
        w for w in words
        if w.lower() not in _SYNTAX_FUNCTION_WORDS and (len(w) > 1 or w.isdigit())
    ]
    skeleton = " ".join(skeleton_tokens)
    variants = [clean_q]
    if skeleton and skeleton.lower() != clean_q.lower() and len(skeleton_tokens) >= 3:
        variants.append(skeleton)
    return variants


def detect_query_type(query: str) -> str:
    return "federated"


# ──────────────────────────────────────────────────────────────────────────────
# Search Providers (Persistent Sessions, Multi-Variant Capable)
# ──────────────────────────────────────────────────────────────────────────────

_DDGS_CLS = None
_DDGS_LOCAL = threading.local()


def _load_ddgs():
    global _DDGS_CLS
    if _DDGS_CLS is None:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        _DDGS_CLS = DDGS
    return _DDGS_CLS


def _get_ddgs():
    client = getattr(_DDGS_LOCAL, "client", None)
    if client is None:
        client = _load_ddgs()(timeout=3)
        _DDGS_LOCAL.client = client
    return client


def _p_ddgs_web(query: str, limit: int) -> list[SearchResult]:
    variants = _build_query_variants(query)
    reg = SEARCH_REGION if (SEARCH_REGION and SEARCH_REGION != "wt-wt") else "us-en"

    def _run_one(q_str: str, lim: int) -> list[SearchResult]:
        rows = []
        try:
            rows = list(_get_ddgs().text(q_str, region=reg, max_results=lim) or [])
        except Exception as e:
            if "no results" in str(e).lower():
                return []
            try:
                _DDGS_LOCAL.client = None
                rows = list(_get_ddgs().text(q_str, region=reg, max_results=lim) or [])
            except Exception:
                return []
        return [
            _mk(r.get("title", ""), (r.get("href") or r.get("url") or "").strip(), r.get("body", ""), "ddgs")
            for r in rows
            if (r.get("href") or r.get("url")) and (r.get("title") or r.get("body"))
        ]

    if len(variants) == 1:
        return _run_one(variants[0], limit)

    f1 = _EXECUTOR.submit(_run_one, variants[0], limit)
    f2 = _EXECUTOR.submit(_run_one, variants[1], max(4, limit // 2))
    out: list[SearchResult] = []
    for f in (f1, f2):
        try:
            out.extend(f.result(timeout=2.4))
        except Exception:
            pass
    return out


def _p_ddgs_news(query: str, limit: int) -> list[SearchResult]:
    clean_q = clean_search_query(query) or query
    reg = SEARCH_REGION if (SEARCH_REGION and SEARCH_REGION != "wt-wt") else "us-en"
    rows = []
    try:
        rows = list(_get_ddgs().news(clean_q, region=reg, max_results=limit) or [])
    except Exception as e:
        if "no results" in str(e).lower():
            return []
        try:
            _DDGS_LOCAL.client = None
            rows = list(_get_ddgs().news(clean_q, region=reg, max_results=limit) or [])
        except Exception:
            return []
    out = []
    for r in rows:
        url = (r.get("url") or r.get("href") or "").strip()
        if url and (r.get("title") or r.get("body")):
            out.append(_mk(r.get("title", ""), url, r.get("body", ""), "ddgs_news", r.get("date", "")))
    return out


def _p_google_news_rss(query: str, limit: int) -> list[SearchResult]:
    variants = _build_query_variants(query)
    q_str = variants[-1]
    hl, gl, ceid = ("en-IN", "IN", "IN:en") if "in" in SEARCH_REGION.lower() else ("en-US", "US", "US:en")
    resp = _HTTP.get(
        "https://news.google.com/rss/search",
        params={"q": q_str, "hl": hl, "gl": gl, "ceid": ceid},
        timeout=2.2,
    )
    root = ET.fromstring(resp.text)
    out = []
    for item in root.findall("./channel/item")[:limit]:
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        pub_date = item.findtext("pubDate") or ""
        desc = _clean_text(item.findtext("description") or "")
        source_el = item.find("source")
        publisher = source_el.text.strip() if (source_el is not None and source_el.text) else "news.google.com"
        source_url = source_el.get("url", "") if source_el is not None else ""
        dom = _domain(source_url) if source_url else publisher
        if publisher and title.endswith(f" - {publisher}"):
            title = title[: -len(f" - {publisher}")].strip()
        snippet = desc if (desc and desc.lower() != title.lower()) else f"{title} (Reported by {publisher})"
        if link and title:
            out.append(_mk(title, link, snippet, "gnews", pub_date, domain_override=dom))
    return out


def _decode_bing_url(href: str) -> str:
    if "bing.com/ck/a" not in href:
        return href
    u = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("u", [""])[0]
    if u.startswith("a1"):
        b = u[2:]
        try:
            return base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)).decode("utf-8", "ignore")
        except Exception:
            return ""
    return ""


def _parse_bing_html(html_text: str, limit: int) -> list[SearchResult]:
    out = []
    if _has_module("selectolax"):
        from selectolax.parser import HTMLParser
        tree = HTMLParser(html_text)
        for node in tree.css("li.b_algo"):
            a = node.css_first("h2 a")
            if not a:
                continue
            url = _decode_bing_url(a.attributes.get("href", ""))
            if not url.startswith("http") or "bing.com" in _domain(url):
                continue
            p = node.css_first(".b_caption p, .b_lineclamp2, .b_lineclamp3, .b_lineclamp4, p")
            title = a.text(separator=" ", strip=True)
            snippet = p.text(separator=" ", strip=True) if p else ""
            out.append(_mk(title, url, snippet, "bing"))
            if len(out) >= limit:
                break
        return out

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html_text, "html.parser")
    for item in soup.select("li.b_algo"):
        a = item.select_one("h2 a")
        p = item.select_one(".b_caption p, p")
        if not a:
            continue
        url = _decode_bing_url(a.get("href", ""))
        if not url.startswith("http") or "bing.com" in _domain(url):
            continue
        out.append(_mk(a.get_text(" ", strip=True), url, p.get_text(" ", strip=True) if p else "", "bing"))
        if len(out) >= limit:
            break
    return out


def _p_bing(query: str, limit: int) -> list[SearchResult]:
    variants = _build_query_variants(query)
    if len(variants) == 1:
        resp = _HTTP.get("https://www.bing.com/search", params={"q": variants[0], "setlang": "en"}, timeout=2.4)
        return _parse_bing_html(resp.text, limit)

    def _one(q_str: str) -> list[SearchResult]:
        r = _HTTP.get("https://www.bing.com/search", params={"q": q_str, "setlang": "en"}, timeout=2.3)
        return _parse_bing_html(r.text, limit)

    f1 = _EXECUTOR.submit(_one, variants[0])
    f2 = _EXECUTOR.submit(_one, variants[1])
    out: list[SearchResult] = []
    for f in (f1, f2):
        try:
            out.extend(f.result(timeout=2.4))
        except Exception:
            pass
    return out


def _p_youtube_metadata(query: str, limit: int) -> list[SearchResult]:
    """
    Queries YouTube's ultra-fast InnerTube JSON endpoint (~250ms) for rich video titles,
    channel names, and snippet descriptions.
    """
    variants = _build_query_variants(query)
    q_str = variants[-1]
    client = {"clientName": "WEB", "clientVersion": _yt_client_version, "hl": "en"}
    if YT_REGION:
        client["gl"] = YT_REGION
    resp = _HTTP.post(
        _YT_SEARCH_URL,
        json_body={"context": {"client": client}, "query": q_str, "params": _YT_P_VIDEOS},
        cookies=_YT_COOKIES,
        timeout=2.2,
        headers={
            "X-YouTube-Client-Name": "1",
            "X-YouTube-Client-Version": _yt_client_version,
            "Origin": "https://www.youtube.com",
            "Content-Type": "application/json",
        },
    )
    data = resp.json()
    out: list[SearchResult] = []
    seen: set[str] = set()
    for vr in _walk_video_renderers(data):
        vid = vr.get("videoId", "")
        if not vid or vid in seen:
            continue
        seen.add(vid)
        title = _runs_text(vr.get("title"))
        channel = _runs_text(vr.get("ownerText")) or _runs_text(vr.get("longBylineText")) or "YouTube"
        desc_parts = []
        for s in vr.get("detailedMetadataSnippets") or []:
            if isinstance(s, dict):
                txt = _runs_text(s.get("snippetText"))
                if txt:
                    desc_parts.append(txt)
        if not desc_parts:
            txt = _runs_text(vr.get("descriptionSnippet"))
            if txt:
                desc_parts.append(txt)
        desc = " ".join(desc_parts).strip()
        snippet = f"[YouTube Video by {channel}] {title}. {desc}".strip()
        out.append(_mk(title, f"https://www.youtube.com/watch?v={vid}", snippet, "yt_meta", domain_override="youtube.com"))
        if len(out) >= min(limit, 5):
            break
    return out


def _p_tavily(query: str, limit: int) -> list[SearchResult]:
    if not TAVILY_API_KEY:
        raise RuntimeError("TAVILY_API_KEY not set")
    resp = _HTTP.post(
        "https://api.tavily.com/search",
        json_body={
            "api_key": TAVILY_API_KEY,
            "query": clean_search_query(query) or query,
            "max_results": limit,
            "search_depth": "basic",
        },
        timeout=2.5,
    )
    out = []
    for r in resp.json().get("results", []):
        if r.get("url"):
            out.append(_mk(r.get("title", ""), r["url"], r.get("content", ""), "tavily", r.get("published_date", "")))
    return out


def _p_brave(query: str, limit: int) -> list[SearchResult]:
    if not BRAVE_API_KEY:
        raise RuntimeError("BRAVE_API_KEY not set")
    resp = _HTTP.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": clean_search_query(query) or query, "count": min(limit, 20)},
        headers={"X-Subscription-Token": BRAVE_API_KEY, "Accept": "application/json"},
        timeout=2.5,
    )
    out = []
    for r in (resp.json().get("web") or {}).get("results", []):
        if r.get("url"):
            out.append(_mk(r.get("title", ""), r["url"], r.get("description", ""), "brave", r.get("page_age", "")))
    return out


def _p_searxng(query: str, limit: int) -> list[SearchResult]:
    if not SEARXNG_URL:
        raise RuntimeError("AMIGO_SEARXNG_URL not set")
    resp = _HTTP.get(
        f"{SEARXNG_URL}/search",
        params={"q": clean_search_query(query) or query, "format": "json"},
        timeout=2.5,
    )
    out = []
    for r in resp.json().get("results", [])[:limit]:
        if r.get("url"):
            out.append(_mk(r.get("title", ""), r["url"], r.get("content", ""), "searxng", r.get("publishedDate", "")))
    return out


_MODULE_OK: dict[str, bool] = {}


def _has_module(name: str) -> bool:
    if name not in _MODULE_OK:
        try:
            __import__(name)
            _MODULE_OK[name] = True
        except Exception:
            _MODULE_OK[name] = False
    return _MODULE_OK[name]


_PROVIDERS: dict[str, tuple[Callable, str, float]] = {
    "ddgs":      (_p_ddgs_web,         "web",   1.10),
    "bing":      (_p_bing,             "web",   1.00),
    "yt_meta":   (_p_youtube_metadata, "media", 0.92),
    "gnews":     (_p_google_news_rss,  "news",  0.95),
    "ddgs_news": (_p_ddgs_news,        "news",  0.90),
    "tavily":    (_p_tavily,           "web",   1.20),
    "brave":     (_p_brave,            "web",   1.15),
    "searxng":   (_p_searxng,          "web",   1.05),
}


def _active_providers(engines: Optional[list[str]] = None) -> list[str]:
    if engines:
        return [e for e in engines if e in _PROVIDERS]
    active = ["ddgs", "bing", "yt_meta", "gnews", "ddgs_news"]
    if TAVILY_API_KEY:
        active.insert(0, "tavily")
    if BRAVE_API_KEY:
        active.insert(0, "brave")
    if SEARXNG_URL:
        active.append("searxng")
    return _select_healthy(active)


# ──────────────────────────────────────────────────────────────────────────────
# Parallel Federated Race + Hybrid T-RRF & BM25-Proximity Reranker
# ──────────────────────────────────────────────────────────────────────────────


def _timed(fn: Callable, *args):
    t0 = time.monotonic()
    out = fn(*args)
    return out, int((time.monotonic() - t0) * 1000)


def _race(names: list[str], query: str, limit: int, budget: float, grace: float):
    if not names:
        return {}, {}
    deadline = time.monotonic() + budget
    futs = {_EXECUTOR.submit(_timed, _PROVIDERS[n][0], query, limit): n for n in names}
    pending, got, timings = set(futs), {}, {}
    grace_anchor = None

    while pending:
        stop_at = deadline if grace_anchor is None else min(deadline, grace_anchor + grace)
        remaining = stop_at - time.monotonic()
        if remaining <= 0:
            break
        done, pending = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
        for f in done:
            name = futs[f]
            try:
                rows, ms = f.result()
            except Exception as e:
                permanent = isinstance(e, ImportError)
                _HEALTH.fail(name, permanent=permanent)
                logger.debug("[WebSearch] %s failed: %s", name, e)
                continue
            timings[name] = ms
            _HEALTH.ok(name)
            if rows:
                got[name] = rows
                categories = {_PROVIDERS[k][1] for k in got}
                if grace_anchor is None and ("web" in categories and len(got) >= 2):
                    grace_anchor = time.monotonic()

    if not got:
        for f in pending:
            _HEALTH.fail(futs[f])
    for f in pending:
        f.cancel()
    return got, timings


def _bm25_and_proximity_score(
    text: str,
    query_tokens: list[str],
    token_weights: dict[str, float],
    query_bigrams: set[tuple[str, str]],
) -> float:
    """
    Computes an Okapi BM25-style lexical saturation score + contiguous bigram proximity bonus
    using purely information-theoretic token weights.
    """
    if not text or not query_tokens:
        return 0.0
    doc_tokens = [t.lower() for t in _WORD_RE.findall(text)]
    if not doc_tokens:
        return 0.0

    doc_len = len(doc_tokens)
    counts = Counter(doc_tokens)
    k1 = 1.5
    b = 0.75
    avgdl = 45.0

    score = 0.0
    total_weight = sum(token_weights.values()) or 1.0

    for tok, w in token_weights.items():
        tf = counts.get(tok, 0)
        if tf > 0:
            norm_tf = (tf * (k1 + 1.0)) / (tf + k1 * (1.0 - b + b * (doc_len / avgdl)))
            score += w * norm_tf

    coverage = sum(token_weights[t] for t in token_weights if counts.get(t, 0) > 0) / total_weight
    score *= (0.5 + 0.8 * (coverage ** 1.5))

    if query_bigrams and len(doc_tokens) >= 2:
        doc_bigrams = {(doc_tokens[i], doc_tokens[i + 1]) for i in range(len(doc_tokens) - 1)}
        bigram_hits = len(query_bigrams & doc_bigrams)
        score += bigram_hits * 1.35

    return score


def _temporal_rrf_fuse(by_engine: dict[str, list[SearchResult]], query: str) -> list[SearchResult]:
    """
    Hybrid Temporal Reciprocal Rank Fusion (T-RRF) + BM25 Proximity Reranker.
    """
    merged: dict[str, SearchResult] = {}
    current_year = datetime.now().year
    recent_years = {str(current_year - 1), str(current_year), str(current_year + 1), str(current_year + 2)}

    clean_q = clean_search_query(query) or query
    raw_tokens = _WORD_RE.findall(clean_q)
    content_tokens = [t.lower() for t in raw_tokens if t.lower() not in _SYNTAX_FUNCTION_WORDS and len(t) > 1]
    if not content_tokens:
        content_tokens = [t.lower() for t in raw_tokens if len(t) > 1]

    token_weights = {t.lower(): _token_specificity(t) for t in raw_tokens if t.lower() in content_tokens}
    query_bigrams = {
        (content_tokens[i], content_tokens[i + 1])
        for i in range(len(content_tokens) - 1)
    }

    for engine, rows in by_engine.items():
        weight = _PROVIDERS.get(engine, (None, "web", 1.0))[2]
        for rank, r in enumerate(rows):
            key = _norm_url(r.url) if r.source != "gnews" else f"title:{r.title.lower()[:65]}"
            rrf_gain = weight / (RRF_K + rank + 1)

            if r.age_days is not None:
                rrf_gain += 0.0030 * math.exp(-r.age_days / 45.0)

            text_blob = f"{r.title} {r.snippet}"
            if any(yr in text_blob for yr in recent_years):
                rrf_gain += 0.0015

            cur = merged.get(key)
            if cur is None:
                r.score = rrf_gain
                merged[key] = r
            else:
                cur.score += rrf_gain
                if len(r.snippet) > len(cur.snippet):
                    cur.snippet = r.snippet
                if r.published and not cur.published:
                    cur.published = r.published

    for r in merged.values():
        title_bm25 = _bm25_and_proximity_score(r.title, content_tokens, token_weights, query_bigrams)
        snip_bm25 = _bm25_and_proximity_score(r.snippet, content_tokens, token_weights, query_bigrams)
        lexical_score = (title_bm25 * 1.35 + snip_bm25) * 0.0045
        r.score += lexical_score

    return sorted(merged.values(), key=lambda r: r.score, reverse=True)


# ──────────────────────────────────────────────────────────────────────────────
# Query-Focused Sliding-Window Deep Passage Extractor
# ──────────────────────────────────────────────────────────────────────────────


def _extract_json_ld_strings(page: str) -> list[str]:
    """Extracts structured answers, descriptions, and article bodies from embedded JSON-LD."""
    out: list[str] = []
    for m in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page, re.I | re.S):
        raw = m.group(1).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        stack = [data]
        while stack:
            curr = stack.pop()
            if isinstance(curr, dict):
                for k, v in curr.items():
                    if k in ("text", "articleBody", "description", "name", "headline") and isinstance(v, str) and len(v) > 25:
                        out.append(_clean_text(v))
                    elif isinstance(v, (dict, list)):
                        stack.append(v)
            elif isinstance(curr, list):
                stack.extend(curr)
    return out


def _extract_raw_blocks(page: str) -> list[str]:
    """Extracts clean paragraph blocks + JSON-LD structured text from raw HTML."""
    blocks = _extract_json_ld_strings(page)

    if _has_module("trafilatura"):
        import trafilatura
        txt = trafilatura.extract(page, include_comments=True, include_tables=True)
        if txt:
            blocks.extend(p.strip() for p in txt.splitlines() if len(p.strip()) > 30)

    if _has_module("selectolax"):
        from selectolax.parser import HTMLParser
        tree = HTMLParser(page)
        for tag in tree.css("script, style, nav, footer, iframe, noscript, svg, form"):
            tag.decompose()
        for node in tree.css("p, li, dd, td, blockquote, article, .comment, .answer, .post-content"):
            t = _WS_RE.sub(" ", node.text(separator=" ", strip=True)).strip()
            if 35 <= len(t) <= 1200:
                blocks.append(t)
    elif not blocks:
        cleaned = _clean_text(page)
        blocks.extend(cleaned[i : i + 450] for i in range(0, len(cleaned), 380))

    unique_blocks: list[str] = []
    seen: set[str] = set()
    for b in blocks:
        sig = b[:80].lower()
        if sig not in seen:
            seen.add(sig)
            unique_blocks.append(b)
    return unique_blocks


def _select_best_passages(page: str, query: str, max_chars: int = MAX_CONTENT_CHARS) -> str:
    """
    Scores paragraph blocks against the query's information-theoretic token weights
    and returns the most relevant passage window.
    """
    blocks = _extract_raw_blocks(page)
    if not blocks:
        return ""

    clean_q = clean_search_query(query) or query
    raw_tokens = _WORD_RE.findall(clean_q)
    content_tokens = [t.lower() for t in raw_tokens if t.lower() not in _SYNTAX_FUNCTION_WORDS and len(t) > 1]
    if not content_tokens:
        content_tokens = [t.lower() for t in raw_tokens if len(t) > 1]
    token_weights = {t.lower(): _token_specificity(t) for t in raw_tokens if t.lower() in content_tokens}
    query_bigrams = {(content_tokens[i], content_tokens[i + 1]) for i in range(len(content_tokens) - 1)}

    windows: list[tuple[float, int, str]] = []
    for idx, blk in enumerate(blocks):
        window_text = blk
        if idx + 1 < len(blocks) and len(window_text) < 280:
            window_text = f"{blk} {blocks[idx + 1]}"
        sc = _bm25_and_proximity_score(window_text, content_tokens, token_weights, query_bigrams)
        if idx < 3:
            sc += 0.35
        windows.append((sc, idx, window_text))

    windows.sort(key=lambda x: x[0], reverse=True)
    selected: list[tuple[int, str]] = []
    used_indices: set[int] = set()
    total_len = 0

    for sc, idx, txt in windows:
        if sc <= 0 and selected:
            break
        if idx in used_indices:
            continue
        selected.append((idx, txt))
        used_indices.add(idx)
        total_len += len(txt) + 3
        if total_len >= max_chars or len(selected) >= 4:
            break

    selected.sort(key=lambda x: x[0])
    return " … ".join(txt for _, txt in selected)[:max_chars]


def _fetch_page_text(url: str, query: str) -> str:
    try:
        resp = _HTTP.get(url, timeout=FETCH_TIMEOUT)
        ctype = (resp.headers.get("content-type") or "").lower()
        if "pdf" in ctype or "audio" in ctype or "video" in ctype or "image" in ctype:
            return ""
        return _select_best_passages(resp.text[:MAX_PAGE_BYTES], query, max_chars=MAX_CONTENT_CHARS)
    except Exception:
        return ""


def _fetch_contents(results: list[SearchResult], query: str) -> None:
    targets = [
        r for r in results[:TOP_K_RESULTS_TO_FETCH]
        if r.source not in ("gnews", "yt_meta") and "youtube.com/watch" not in r.url
    ]
    if not targets:
        return
    futs = {_EXECUTOR.submit(_fetch_page_text, r.url, query): r for r in targets}
    done, pending = wait(list(futs), timeout=FETCH_DEADLINE_S)
    for f in done:
        try:
            futs[f].fetched_content = f.result()
        except Exception:
            pass
    for f in pending:
        f.cancel()


def _should_auto_deep_fetch(query: str, results: list[SearchResult], fast_mode: bool) -> bool:
    if fast_mode or not results:
        return False
    clean_q = clean_search_query(query) or query
    content_tokens = [
        t.lower() for t in _WORD_RE.findall(clean_q)
        if t.lower() not in _SYNTAX_FUNCTION_WORDS and len(t) > 1
    ]
    if len(content_tokens) >= 4:
        return True
    top_blob = " ".join(f"{r.title} {r.snippet}".lower() for r in results[:2])
    missing = [t for t in content_tokens if t not in top_blob]
    return bool(missing)


# ──────────────────────────────────────────────────────────────────────────────
# Public Search API
# ──────────────────────────────────────────────────────────────────────────────


def search_web(
    query: str,
    max_results: int = TOP_K_FINAL_SNIPPETS,
    engines: Optional[list[str]] = None,
    fetch_content: Optional[bool] = None,
    use_cache: bool = True,
    fast_mode: bool = False,
    freshness: Optional[str] = None,
) -> SearchResponse:
    """
    Federated parallel web + live news + video metadata search with automatic
    query-focused deep passage extraction for complex questions.
    """
    t0 = time.monotonic()
    query = (query or "").strip()
    if not query:
        return SearchResponse(query="", results=[], query_type="empty")

    if fast_mode:
        fetch_content = False
        max_results = min(max_results, TOP_K_FINAL_SNIPPETS)

    cache_key = (query.lower(), bool(fetch_content), bool(fast_mode), tuple(sorted(engines or ())))
    if use_cache:
        cached = _SEARCH_CACHE.get(cache_key)
        if cached is not None:
            return replace(cached, results=list(cached.results[:max_results]), cache_hit=True, search_time_ms=0)

    providers = _active_providers(engines)
    budget = _BUDGETS["fast" if fast_mode else "full"]
    grace = MERGE_GRACE_FAST_S if fast_mode else MERGE_GRACE_S
    per_engine = PER_ENGINE_FAST if fast_mode else PER_ENGINE_FULL

    by_engine, timings = _race(providers, query, per_engine, budget, grace)
    fused = _temporal_rrf_fuse(by_engine, query)[:PER_ENGINE_FULL]
    final = fused[:max_results]

    do_fetch = fetch_content if fetch_content is not None else _should_auto_deep_fetch(query, final, fast_mode)
    if do_fetch and final:
        _fetch_contents(final, query)
        clean_q = clean_search_query(query) or query
        raw_tokens = _WORD_RE.findall(clean_q)
        content_tokens = [t.lower() for t in raw_tokens if t.lower() not in _SYNTAX_FUNCTION_WORDS and len(t) > 1]
        token_weights = {t.lower(): _token_specificity(t) for t in raw_tokens if t.lower() in content_tokens}
        query_bigrams = {(content_tokens[i], content_tokens[i + 1]) for i in range(len(content_tokens) - 1)}
        for r in final:
            if r.fetched_content:
                r.score += _bm25_and_proximity_score(
                    r.fetched_content, content_tokens, token_weights, query_bigrams
                ) * 0.0035
        final.sort(key=lambda r: r.score, reverse=True)

    resp = SearchResponse(
        query=query,
        results=final,
        query_type="federated",
        search_time_ms=int((time.monotonic() - t0) * 1000),
        engines_used=list(by_engine),
    )
    if use_cache and fused:
        _SEARCH_CACHE.set(cache_key, replace(resp, results=final), CACHE_TTL_S)

    logger.info(
        "[WebSearch] %r -> %d results in %dms (deep_fetch=%s | %s)",
        query[:60],
        len(final),
        resp.search_time_ms,
        bool(do_fetch),
        ", ".join(f"{k}:{v}ms" for k, v in timings.items()) or "no engine answered",
    )
    return resp


def format_for_llm(response: SearchResponse, max_chars: int = 4800) -> str:
    """Formats federated web, news, and deep-extracted passages for accurate LLM synthesis."""
    if not response.results:
        return "No search results found."
    now_str = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"Live Web, Video & Deep-Passage Search Results for: {response.query!r} (Current Date: {now_str})",
        "(Synthesize specific names, titles, facts, and timestamps directly from snippets and deep excerpts.)",
    ]
    for i, r in enumerate(response.results, 1):
        when = f" [Published: {r.published}]" if r.published else ""
        lines.append(f"{i}. {r.title}{when} ({r.domain})\n   Snippet: {r.snippet}")
        if r.fetched_content:
            lines.append(f"   Deep Page Excerpt: {r.fetched_content[:950]}")
    return "\n".join(lines)[:max_chars]


def scrape_web_info(query: str, max_results: int = 5) -> str:
    response = search_web(query, max_results=max_results)
    parts = []
    for r in response.results:
        piece = f"{r.title}: {r.snippet}"
        if r.fetched_content:
            piece += f" [Excerpt: {r.fetched_content[:450]}]"
        parts.append(piece)
    return " | ".join(parts)[:3600]


def searchGoogle(query: str) -> None:
    q = (query or "").strip()
    if q:
        webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote_plus(q))


async def async_search_web(query: str, **kwargs) -> SearchResponse:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_EXECUTOR, lambda: search_web(query, **kwargs))


# ──────────────────────────────────────────────────────────────────────────────
# YouTube Resolution Engine
# ──────────────────────────────────────────────────────────────────────────────


def _extract_json_blob(page: str, var: str):
    m = re.search(rf"{var}(?:\"\])?\s*=\s*", page)
    if not m:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(page, m.end())
        return obj
    except ValueError:
        return None


def _yt_innertube(query: str, params: Optional[str]) -> list[YTVideo]:
    client = {"clientName": "WEB", "clientVersion": _yt_client_version, "hl": "en"}
    if YT_REGION:
        client["gl"] = YT_REGION
    body = {"context": {"client": client}, "query": query}
    if params:
        body["params"] = params
    resp = _HTTP.post(
        _YT_SEARCH_URL,
        json_body=body,
        cookies=_YT_COOKIES,
        timeout=2.5,
        headers={
            "X-YouTube-Client-Name": "1",
            "X-YouTube-Client-Version": _yt_client_version,
            "Origin": "https://www.youtube.com",
            "Content-Type": "application/json",
        },
    )
    return _videos_from_json(resp.json(), "innertube")


def _yt_html(query: str, params: Optional[str]) -> list[YTVideo]:
    global _yt_client_version
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query)
    if params:
        url += "&sp=" + urllib.parse.quote(params, safe="")
    resp = _HTTP.get(url, cookies=_YT_COOKIES, timeout=3.0)
    m = re.search(r'"INNERTUBE_CONTEXT_CLIENT_VERSION"\s*:\s*"([\d.]+)"', resp.text)
    if m:
        _yt_client_version = m.group(1)
    data = _extract_json_blob(resp.text, "ytInitialData")
    return _videos_from_json(data, "html") if data else []


def _yt_ytdlp(query: str, latest: bool = False, n: int = 5) -> list[YTVideo]:
    import yt_dlp
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": True, "socket_timeout": 4}
    prefix = "ytsearchdate" if latest else "ytsearch"
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"{prefix}{n}:{query}", download=False) or {}
    out = []
    for e in info.get("entries") or []:
        if e and e.get("id"):
            dur = e.get("duration")
            out.append(YTVideo(
                video_id=e["id"],
                title=e.get("title") or "",
                channel=e.get("channel") or e.get("uploader") or "YouTube",
                is_live=e.get("live_status") == "is_live" or bool(e.get("is_live")),
                duration_s=int(dur) if dur else None,
                source="ytdlp",
            ))
    return out


_YTM = None
_YTM_LOCK = threading.Lock()


def _get_ytmusic():
    global _YTM
    with _YTM_LOCK:
        if _YTM is None:
            from ytmusicapi import YTMusic
            _YTM = YTMusic()
        return _YTM


def _yt_music_songs(query: str, limit: int = 3) -> list[YTVideo]:
    rows = _get_ytmusic().search(query, filter="songs", limit=limit) or []
    out = []
    for r in rows:
        if r.get("videoId"):
            artists = ", ".join(a.get("name", "") for a in (r.get("artists") or []) if a.get("name"))
            out.append(YTVideo(
                video_id=r["videoId"],
                title=r.get("title", ""),
                channel=artists or "YouTube Music",
                duration_s=r.get("duration_seconds"),
                source="ytmusic",
            ))
    return out


def _run_stages(stages: list[tuple[str, Callable[[], list[YTVideo]]]], deadline: float) -> list[YTVideo]:
    by_name = dict(stages)
    for name in _select_healthy([n for n, _ in stages]):
        if time.monotonic() >= deadline:
            break
        try:
            videos = by_name[name]()
        except Exception as e:
            _HEALTH.fail(name, permanent=isinstance(e, ImportError))
            continue
        _HEALTH.ok(name)
        if videos:
            return videos
    return []


def _yt_search(query: str, deadline: float, params: Optional[str] = None, latest: bool = False) -> list[YTVideo]:
    return _run_stages([
        ("yt_innertube", lambda: _yt_innertube(query, params)),
        ("yt_html", lambda: _yt_html(query, params)),
        ("yt_ytdlp", lambda: _yt_ytdlp(query, latest=latest)),
    ], deadline)


_MEDIA_COMMAND = re.compile(
    r"^(?:" + _LEAD_IN + r")+(?:open\s+youtube\s+(?:and\s+)?)?(?:play|put\s+on|listen\s+to|stream|watch)\s+", re.I
)
_MEDIA_TRAIL = re.compile(r"\s+(?:on|in)\s+youtube(?:\s+music)?$", re.I)
_RE_LIVE_PHRASE = re.compile(r"\b(?:live\s*stream|livestream|live\s+now|currently\s+live)\b", re.I)
_RE_LATEST_PHRASE = re.compile(r"\b(?:latest|newest|most\s+recent)\b\s*(?:videos?|uploads?|vids?)?", re.I)


def _normalize_media_query(q: str) -> str:
    out = _MEDIA_COMMAND.sub("", (q or "").strip())
    out = _MEDIA_TRAIL.sub("", out).strip()
    return out or (q or "").strip()


def _pick(videos: list[YTVideo], kind: str) -> Optional[YTVideo]:
    if not videos:
        return None
    if kind == "song":
        for v in videos:
            if v.duration_s is None or 60 <= v.duration_s <= 720:
                return v
    return videos[0]


def resolve_youtube_video(
    query: str,
    kind: str = "auto",
    live: Optional[bool] = None,
    latest: Optional[bool] = None,
    timeout: float = YT_BUDGET_S,
) -> dict:
    raw = (query or "").strip()
    q = _normalize_media_query(raw)
    kind = kind if kind in ("auto", "song", "video", "live") else "auto"

    if live is None:
        live = kind == "live"
        if not live and _RE_LIVE_PHRASE.search(q):
            live, q = True, _RE_LIVE_PHRASE.sub(" ", q)
    if latest is None:
        latest = bool(_RE_LATEST_PHRASE.search(q))
        if latest:
            q = _RE_LATEST_PHRASE.sub(" ", q)
    q = _WS_RE.sub(" ", q).strip() or raw

    result = {
        "url": "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(q),
        "title": q.title(),
        "channel": "YouTube",
        "video_id": "",
        "query": q,
        "is_live": False,
        "not_live_fallback": False,
        "resolved": False,
        "source": "search_page",
    }

    cache_key = (q.lower(), kind)
    if not live and not latest:
        hit = _YT_CACHE.get(cache_key)
        if hit:
            return dict(hit)

    deadline = time.monotonic() + timeout
    video: Optional[YTVideo] = None
    not_live = False

    if live:
        vids = _yt_search(q, deadline, params=_YT_P_LIVE)
        video = next((v for v in vids if v.is_live), None)
        if video is None:
            vids = _yt_search(q + " live", deadline)
            video = next((v for v in vids if v.is_live), None)
            if video is None and vids:
                video, not_live = vids[0], True
    else:
        if latest:
            video = _pick(_yt_search(q, deadline, params=_YT_P_NEWEST, latest=True), kind)
        if video is None and kind == "song":
            video = _pick(_run_stages([("yt_music", lambda: _yt_music_songs(q))], deadline), kind)
        if video is None:
            video = _pick(_yt_search(q, deadline, params=_YT_P_VIDEOS), kind)

    if video:
        result.update(
            video_id=video.video_id,
            title=video.title or result["title"],
            channel=video.channel or "YouTube",
            url=f"https://www.youtube.com/watch?v={video.video_id}&autoplay=1",
            is_live=bool(video.is_live) and not not_live,
            not_live_fallback=not_live,
            resolved=True,
            source=video.source,
        )
        if not live and not latest:
            _YT_CACHE.set(cache_key, dict(result), YT_CACHE_TTL_S)
    return result


def searchYoutube(query: str, **kwargs) -> str:
    info = resolve_youtube_video(query, **kwargs)
    webbrowser.open(info["url"])
    return info["url"]


# ──────────────────────────────────────────────────────────────────────────────
# Live Market Quotes (Yahoo Finance API)
# ──────────────────────────────────────────────────────────────────────────────

_YF_SEARCH = "https://query2.finance.yahoo.com/v1/finance/search"
_YF_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_QUOTE_TYPES = frozenset({"EQUITY", "ETF", "INDEX", "CRYPTOCURRENCY", "CURRENCY", "MUTUALFUND", "FUTURE"})
_CCY_SYMBOL = {"USD": "$", "INR": "\u20b9", "EUR": "\u20ac", "GBP": "\u00a3"}
_QUOTE_CACHE = _TTLCache(64)
QUOTE_TTL_S = 30


@dataclass
class Quote:
    symbol: str
    name: str
    price: float
    currency: str = ""
    previous_close: Optional[float] = None
    exchange: str = ""
    as_of: Optional[datetime] = None
    day_high: Optional[float] = None
    day_low: Optional[float] = None
    volume: Optional[int] = None
    fifty_two_week_high: Optional[float] = None
    fifty_two_week_low: Optional[float] = None
    history: list[float] = field(default_factory=list)
    timestamps: list[int] = field(default_factory=list)

    @property
    def change(self) -> Optional[float]:
        return None if self.previous_close in (None, 0) else self.price - self.previous_close

    @property
    def change_pct(self) -> Optional[float]:
        return None if self.change is None else self.change / self.previous_close * 100

    @property
    def url(self) -> str:
        return "https://finance.yahoo.com/quote/" + urllib.parse.quote(self.symbol, safe="")


def _yf_get(url: str, params: dict) -> dict:
    return _HTTP.get(url, params=params, headers={"Accept": "application/json"}, timeout=2.5).json()


def _resolve_symbol(query: str) -> Optional[tuple[str, str]]:
    data = _yf_get(_YF_SEARCH, {"q": query, "quotesCount": 6, "newsCount": 0, "lang": "en-US"})
    for r in data.get("quotes") or []:
        if r.get("symbol") and r.get("quoteType") in _QUOTE_TYPES:
            return r["symbol"], (r.get("shortname") or r.get("longname") or r["symbol"])
    return None


def get_stock_quote(query: str, range_val: str = "1d") -> Optional[Quote]:
    q = (query or "").strip()
    if not q:
        return None
    cache_key = f"{q.lower()}:{range_val}"
    cached = _QUOTE_CACHE.get(cache_key)
    if cached:
        return cached
    try:
        resolved = _resolve_symbol(q)
        if not resolved:
            return None
        symbol, name = resolved

        intervals = {"1d": "5m", "5d": "15m", "1mo": "1d", "3mo": "1d", "1y": "1wk"}
        interval = intervals.get(range_val, "5m")
        data = _yf_get(
            _YF_CHART.format(symbol=urllib.parse.quote(symbol, safe="")),
            {"range": range_val, "interval": interval},
        )
        result = (data.get("chart") or {}).get("result") or []
        meta = (result[0].get("meta") if result else None) or {}
        price = meta.get("regularMarketPrice")
        if price is None:
            return None

        quotes_data = (result[0].get("indicators", {}).get("quote", [{}])[0]) if result else {}
        closes = quotes_data.get("close", [])
        history = [round(float(c), 2) for c in closes if c is not None]
        timestamps = [int(t) for t in (result[0].get("timestamp") or []) if t is not None]

        if range_val == "1d" and len(history) < 8:
            try:
                data_5d = _yf_get(
                    _YF_CHART.format(symbol=urllib.parse.quote(symbol, safe="")),
                    {"range": "5d", "interval": "15m"},
                )
                res_5d = (data_5d.get("chart") or {}).get("result") or []
                if res_5d:
                    q_5d = (res_5d[0].get("indicators", {}).get("quote", [{}])[0])
                    closes_5d = q_5d.get("close", [])
                    h_5d = [round(float(c), 2) for c in closes_5d if c is not None]
                    if len(h_5d) > len(history):
                        history = h_5d
                        timestamps = [int(t) for t in (res_5d[0].get("timestamp") or []) if t is not None]
            except Exception:
                pass

        as_of = None
        if meta.get("regularMarketTime"):
            try:
                from zoneinfo import ZoneInfo
                as_of = datetime.fromtimestamp(
                    meta["regularMarketTime"], ZoneInfo(meta.get("exchangeTimezoneName") or "UTC")
                )
            except Exception:
                as_of = datetime.fromtimestamp(meta["regularMarketTime"])
        quote = Quote(
            symbol=symbol,
            name=name,
            price=float(price),
            currency=meta.get("currency") or "USD",
            previous_close=meta.get("previousClose") or meta.get("chartPreviousClose"),
            exchange=meta.get("fullExchangeName") or meta.get("exchangeName") or "",
            as_of=as_of,
            day_high=meta.get("regularMarketDayHigh"),
            day_low=meta.get("regularMarketDayLow"),
            volume=meta.get("regularMarketVolume"),
            fifty_two_week_high=meta.get("fiftyTwoWeekHigh"),
            fifty_two_week_low=meta.get("fiftyTwoWeekLow"),
            history=history,
            timestamps=timestamps,
        )
        _QUOTE_CACHE.set(cache_key, quote, QUOTE_TTL_S)
        return quote
    except Exception as e:
        logger.warning("[Quote] %r failed: %s", q, e)
        return None


def format_quote(q: Quote) -> str:
    sym = _CCY_SYMBOL.get(q.currency, f"{q.currency} " if q.currency else "")
    text = f"{q.name} is at {sym}{q.price:,.2f}"
    if q.change_pct is not None:
        text += f", {'up' if q.change >= 0 else 'down'} {abs(q.change_pct):.2f} percent from the previous close"
    if q.as_of:
        text += f". Last trade was at {q.as_of.strftime('%I:%M %p').lstrip('0')} on {q.as_of.strftime('%b')} {q.as_of.day}"
    return text + "."


# ──────────────────────────────────────────────────────────────────────────────
# Automatic Background Warmup on Import
# ──────────────────────────────────────────────────────────────────────────────


def warmup() -> None:
    """Pre-warms DDGS client and HTTP/2 TLS sockets in the background on startup."""
    def _w():
        for fn in (_get_ddgs, lambda: _has_module("selectolax")):
            try:
                fn()
            except Exception:
                pass
        for url in ("https://news.google.com/", "https://www.bing.com/", "https://www.youtube.com/"):
            try:
                _HTTP.get(url, timeout=1.5)
            except Exception:
                pass

    _EXECUTOR.submit(_w)


def shutdown() -> None:
    _EXECUTOR.shutdown(wait=False, cancel_futures=True)
    _SEARCH_CACHE.clear()
    _YT_CACHE.clear()


warmup()
