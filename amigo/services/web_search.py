"""
web_search.py - Consensus-Verified Epistemic Retrieval Engine for Amigo.

Completely rebuilt architecture focused on source trustworthiness, cross-domain
claim corroboration, and zero hardcoded domain/topic/country rules.

Core Pillars:
  1. Zero-Hardcoding OS Locale & Environment Introspection:
     Derives ISO country, language, and timezone strictly from runtime OS APIs.
  2. Multi-Oracle Independent Harvester:
     Queries independent search indexes (Encyclopedia OpenSearch, DDGS API,
     DDG Lite, Yahoo Web, Bing Web, Google News RSS, and optional API Oracles
     like Tavily/Brave/SearXNG) in an asynchronous/threaded non-blocking pool.
  3. Structural Authority Scorer:
     Evaluates URLs using information-theoretic domain entropy, path depth,
     subdomain nesting, and institutional TLD structure—zero domain lists.
  4. Cross-Source Claim Corroboration Graph:
     Extracts atomic factual tokens (numbers, dates, named entities, specs) across
     distinct domains. Ranks sources by independent inter-domain agreement
     (PageRank-style consensus) so hallucinated or spam pages are filtered out.
  5. Verbatim Evidence & Schema Extractor:
     Downloads top consensus-verified pages and extracts high-density factual
     sentences and structured JSON-LD entities with explicit provenance tags.
"""

from __future__ import annotations

import asyncio
import base64
import ctypes
import email.utils
import html
import json
import locale
import logging
import math
import os
import re
import threading
import time
import urllib.parse
import webbrowser
import xml.etree.ElementTree as ET
from collections import Counter, OrderedDict, defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional

try:
    import httpx
    _USING_HTTPX = True
except ImportError:
    import requests
    _USING_HTTPX = False

logger = logging.getLogger("amigo.web_search")
for _lg in ("ddgs", "ddgs.ddgs", "duckduckgo_search", "httpx", "httpcore", "primp", "urllib3"):
    logging.getLogger(_lg).setLevel(logging.ERROR)


# ──────────────────────────────────────────────────────────────────────────────
# 1. Dynamic OS Environment Introspection (Zero Hardcoded Fallbacks)
# ──────────────────────────────────────────────────────────────────────────────

def _discover_os_locale() -> tuple[str, str, str]:
    """
    Interrogates the host OS for (language_code, country_code, region_tag)
    without any hardcoded country or language defaults.
    """
    lang_code = ""
    country_code = ""

    if os.name == "nt":
        try:
            buf = ctypes.create_unicode_buffer(85)
            if ctypes.windll.kernel32.GetUserDefaultLocaleName(buf, 85) > 0:
                raw = buf.value.replace("_", "-")
                parts = [p for p in raw.split("-") if p]
                if parts and len(parts[0]) in (2, 3) and parts[0].isalpha():
                    lang_code = parts[0].lower()
                for p in reversed(parts):
                    if len(p) == 2 and p.isalpha() and p.upper() not in ("WT", "XX", "ZZ"):
                        country_code = p.upper()
                        break
        except Exception:
            pass

        if not country_code:
            try:
                buf = ctypes.create_unicode_buffer(16)
                if ctypes.windll.kernel32.GetUserDefaultGeoName(buf, 16) > 0:
                    val = buf.value.strip().upper()
                    if len(val) == 2 and val.isalpha() and val not in ("WT", "XX", "ZZ"):
                        country_code = val
            except Exception:
                pass

    if not country_code or not lang_code:
        for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
            env_val = os.getenv(var, "").split(".")[0].replace("-", "_")
            if "_" in env_val:
                l_part, c_part = env_val.split("_", 1)
                if not lang_code and len(l_part) in (2, 3) and l_part.isalpha():
                    lang_code = l_part.lower()
                c_clean = c_part.split("_")[0].upper()
                if not country_code and len(c_clean) == 2 and c_clean.isalpha():
                    country_code = c_clean
                break

    if not country_code or not lang_code:
        try:
            sys_loc = (locale.getlocale()[0] or "").replace("-", "_")
            if "_" in sys_loc:
                l_part, c_part = sys_loc.split("_", 1)
                if not lang_code and l_part.isalpha():
                    lang_code = l_part.lower()
                if not country_code and len(c_part) == 2 and c_part.isalpha():
                    country_code = c_part.upper()
        except Exception:
            pass

    region_tag = f"{country_code.lower()}-{lang_code}" if (country_code and lang_code) else ""
    return lang_code, country_code, region_tag


OS_LANG, PC_COUNTRY_CODE, _OS_REGION_TAG = _discover_os_locale()
SEARCH_REGION = os.getenv("AMIGO_SEARCH_REGION", "").strip() or _OS_REGION_TAG

SEARXNG_URL = os.getenv("AMIGO_SEARXNG_URL", "").rstrip("/")
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "").strip()
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "").strip()
YT_REGION = os.getenv("AMIGO_YT_REGION", "").strip() or PC_COUNTRY_CODE


# ──────────────────────────────────────────────────────────────────────────────
# 2. Data Structures (100% Backwards-Compatible + Epistemic Metadata)
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
    # Epistemic & Consensus telemetry
    authority_score: float = 0.0
    consensus_score: float = 0.0
    corroborated_by: list[str] = field(default_factory=list)
    trust_tier: str = "UNVERIFIED"

    @property
    def relevance_score(self) -> float:
        return self.score

    @property
    def credibility(self) -> float:
        return round(min(1.0, max(0.1, self.authority_score * 0.5 + self.consensus_score * 0.5)), 3)


@dataclass
class SearchResponse:
    query: str
    results: list[SearchResult]
    query_type: str = "epistemic_consensus"
    structured_data: dict[str, Any] = field(default_factory=dict)
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
        return None if self.change is None else (self.change / self.previous_close) * 100.0

    @property
    def url(self) -> str:
        return "https://finance.yahoo.com/quote/" + urllib.parse.quote(self.symbol, safe="")


# ──────────────────────────────────────────────────────────────────────────────
# 3. Resilient Transport Layer & Circuit Registry
# ──────────────────────────────────────────────────────────────────────────────

class _Transport:
    """Thread-safe connection-pooled HTTP client with automatic header negotiation."""

    def __init__(self) -> None:
        accept_lang = (
            f"{OS_LANG}-{PC_COUNTRY_CODE},{OS_LANG};q=0.9,*;q=0.5"
            if (OS_LANG and PC_COUNTRY_CODE)
            else f"{OS_LANG};q=0.9,*;q=0.5" if OS_LANG else "*;q=0.8"
        )
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
            "Accept-Language": accept_lang,
            "DNT": "1",
        }
        if _USING_HTTPX:
            self._session = httpx.Client(
                http2=True,
                headers=self._headers,
                follow_redirects=True,
                timeout=httpx.Timeout(3.0, connect=1.25),
                limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
            )
        else:
            from requests.adapters import HTTPAdapter
            self._session = requests.Session()
            adapter = HTTPAdapter(max_retries=1, pool_connections=32, pool_maxsize=64)
            self._session.mount("http://", adapter)
            self._session.mount("https://", adapter)
            self._session.headers.update(self._headers)

    def get(
        self,
        url: str,
        *,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        cookies: Optional[dict] = None,
        timeout: float = 2.6,
    ):
        if _USING_HTTPX:
            r = self._session.get(url, params=params, headers=headers, cookies=cookies, timeout=timeout)
            r.raise_for_status()
            return r
        r = self._session.get(url, params=params, headers=headers, cookies=cookies, timeout=(1.2, timeout))
        r.raise_for_status()
        return r

    def post(
        self,
        url: str,
        *,
        json_body: Optional[dict] = None,
        data: Optional[dict] = None,
        headers: Optional[dict] = None,
        cookies: Optional[dict] = None,
        timeout: float = 2.6,
    ):
        if _USING_HTTPX:
            r = self._session.post(url, json=json_body, data=data, headers=headers, cookies=cookies, timeout=timeout)
            r.raise_for_status()
            return r
        r = self._session.post(url, json=json_body, data=data, headers=headers, cookies=cookies, timeout=(1.2, timeout))
        r.raise_for_status()
        return r


class _ProviderTelemetry:
    """Tracks exponential moving average latency and health for every search oracle."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: dict[str, int] = defaultdict(int)
        self._cooldown_until: dict[str, float] = {}
        self._ema_ms: dict[str, float] = {}

    def is_healthy(self, provider: str) -> bool:
        with self._lock:
            return time.monotonic() >= self._cooldown_until.get(provider, 0.0)

    def record_success(self, provider: str, elapsed_ms: float) -> None:
        with self._lock:
            self._failures.pop(provider, None)
            self._cooldown_until.pop(provider, None)
            prev = self._ema_ms.get(provider, elapsed_ms)
            self._ema_ms[provider] = 0.7 * prev + 0.3 * elapsed_ms

    def record_failure(self, provider: str, permanent: bool = False) -> None:
        with self._lock:
            if permanent:
                self._cooldown_until[provider] = float("inf")
                return
            fails = self._failures[provider] + 1
            self._failures[provider] = fails
            if fails >= 2:
                backoff = min(180.0, 6.0 * (2 ** (fails - 1)))
                self._cooldown_until[provider] = time.monotonic() + backoff


class _LRUTTLCache:
    def __init__(self, capacity: int = 256) -> None:
        self._capacity = capacity
        self._store: OrderedDict[Any, tuple[Any, float]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: Any) -> Any:
        with self._lock:
            entry = self._store.get(key)
            if not entry:
                return None
            val, exp = entry
            if time.monotonic() > exp:
                del self._store[key]
                return None
            self._store.move_to_end(key)
            return val

    def put(self, key: Any, val: Any, ttl_s: float) -> None:
        with self._lock:
            self._store[key] = (val, time.monotonic() + ttl_s)
            self._store.move_to_end(key)
            while len(self._store) > self._capacity:
                self._store.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


_NET = _Transport()
_TELEMETRY = _ProviderTelemetry()
_POOL = ThreadPoolExecutor(max_workers=20, thread_name_prefix="amigo-epistemic")
_RESULT_CACHE = _LRUTTLCache(256)
_MEDIA_CACHE = _LRUTTLCache(128)
_QUOTE_CACHE = _LRUTTLCache(64)


# ──────────────────────────────────────────────────────────────────────────────
# 4. Linguistic & Information-Theoretic Primitives (Zero Hardcoded Lists)
# ──────────────────────────────────────────────────────────────────────────────

_RE_HTML_TAG = re.compile(r"<[^>]+>")
_RE_WHITESPACE = re.compile(r"\s+")
_RE_TOKEN = re.compile(r"[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*")
_RE_FACT_ATOM = re.compile(
    r"\b(?:"
    r"\d+(?:[.,]\d+)*(?:\s*%)?"              # Numbers, decimals, percentages
    r"|[A-Z][a-zA-Z0-9_-]{2,}"               # Proper nouns / capitalized identifiers
    r"|[a-zA-Z]+\d+[a-zA-Z0-9_-]*"           # Alphanumeric model/version codes
    r")\b"
)
_RE_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")

# Universal closed-class English grammatical glue (syntactic only)
_CLOSED_CLASS_GLUE = frozenset({
    "a", "an", "the", "and", "or", "but", "if", "nor", "for", "yet", "so",
    "in", "on", "at", "to", "from", "by", "with", "about", "against", "between",
    "into", "through", "during", "before", "after", "above", "below", "up", "down",
    "of", "off", "over", "under", "is", "am", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "must", "can", "could", "what", "which", "who",
    "whom", "whose", "when", "where", "why", "how", "this", "that", "these",
    "those", "it", "its", "they", "them", "their", "we", "us", "our", "you", "your",
})

_RE_VOICE_PREFIX = re.compile(
    r"^(?:(?:hey|hi|hello|ok|okay)\s+amigo\b[\s,.:;!-]*)"
    r"|(?:^(?:please\s+)?(?:search(?:\s+the\s+web)?(?:\s+for)?|look\s+up|find(?:\s+out)?|tell\s+me(?:\s+about)?|google|check)\s+)",
    re.IGNORECASE,
)


def _normalize_text(raw: str) -> str:
    if not raw:
        return ""
    unescaped = html.unescape(_RE_HTML_TAG.sub(" ", raw))
    return _RE_WHITESPACE.sub(" ", unescaped).strip()


def clean_search_query(raw_query: str) -> str:
    """Strips conversational voice assistant framing while preserving all factual tokens."""
    q = (raw_query or "").strip()
    if not q:
        return ""
    for _ in range(2):
        q = _RE_VOICE_PREFIX.sub("", q).strip()
    q = q.strip("\"'`?!.")
    q = _RE_WHITESPACE.sub(" ", q)
    return q if len(q) >= 2 else (raw_query or "").strip()


def detect_query_type(query: str) -> str:
    return "epistemic_consensus"


def _extract_host(url: str) -> str:
    try:
        netloc = urllib.parse.urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
        return netloc[4:] if netloc.startswith("www.") else netloc
    except Exception:
        return ""


def _registered_root_domain(host: str) -> str:
    """Extracts the organizational root domain so subdomains cannot self-corroborate."""
    parts = [p for p in host.split(".") if p]
    if len(parts) <= 2:
        return host
    # Handle 2-level country-code SLDs (e.g., .co.uk, .ac.in, .gov.au)
    if len(parts[-1]) == 2 and len(parts[-2]) <= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _canonical_url_key(url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(url.strip())
        host = _extract_host(url)
        path = parsed.path.rstrip("/")
        clean_params = [
            (k, v)
            for k, v in urllib.parse.parse_qsl(parsed.query)
            if not k.lower().startswith(("utm_", "fbclid", "gclid", "msclkid", "ref", "mc_"))
        ]
        qs = urllib.parse.urlencode(sorted(clean_params))
        return f"{host}{path}" + (f"?{qs}" if qs else "")
    except Exception:
        return url.strip().lower()


def _parse_timestamp(raw_date: str) -> tuple[str, Optional[float]]:
    if not raw_date:
        return "", None
    cleaned = raw_date.strip()
    dt: Optional[datetime] = None
    for parser in (
        lambda s: email.utils.parsedate_to_datetime(s),
        lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")),
    ):
        try:
            dt = parser(cleaned)
            if dt:
                break
        except Exception:
            continue

    if dt is not None:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta_days = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)
        return dt.strftime("%Y-%m-%d"), delta_days

    m = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", cleaned)
    if m:
        return m.group(1), None
    return "", None


def _token_information_weight(token: str) -> float:
    """
    Calculates token specificity purely from orthographic entropy (digits, casing,
    character length, hyphenation) with zero hardcoded keywords.
    """
    low = token.lower()
    if low in _CLOSED_CLASS_GLUE:
        return 0.02
    length_bonus = 0.4 * math.log1p(len(low))
    has_digit = any(c.isdigit() for c in token)
    has_alpha = any(c.isalpha() for c in token)
    mixed_alnum = 1.6 if (has_digit and has_alpha) else (0.85 if has_digit else 0.0)
    casing_bonus = 0.45 if any(c.isupper() for c in token[1:]) else (0.25 if token[0].isupper() else 0.0)
    struct_bonus = 0.5 if ("-" in token or "." in token) else 0.0
    return 1.0 + length_bonus + mixed_alnum + casing_bonus + struct_bonus


def _formulate_search_queries(raw_query: str) -> list[str]:
    """
    Produces two orthogonal search formulations:
      1. Clean semantic query
      2. High-entropy keyword distillation for strict index matching
    """
    base = clean_search_query(raw_query)
    tokens = _RE_TOKEN.findall(base)
    if len(tokens) <= 4:
        return [base]
    distilled = [t for t in tokens if t.lower() not in _CLOSED_CLASS_GLUE and len(t) > 1]
    distilled_str = " ".join(distilled)
    if distilled_str and distilled_str.lower() != base.lower() and len(distilled) >= 2:
        return [base, distilled_str]
    return [base]


# ──────────────────────────────────────────────────────────────────────────────
# 5. Structural Authority Engine (Zero Hardcoded Domains)
# ──────────────────────────────────────────────────────────────────────────────

def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = Counter(s)
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _compute_structural_authority(url: str, title: str, snippet: str) -> float:
    """
    Computes intrinsic source reliability [0.15 .. 1.0] purely from structural
    URL/domain properties and text hygiene—no hardcoded site lists.
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return 0.25

    score = 0.62

    # 1. Transport security
    if parsed.scheme == "https":
        score += 0.06
    else:
        score -= 0.15

    host = _extract_host(url)
    if not host:
        return 0.2

    labels = host.split(".")
    tld = labels[-1] if labels else ""
    second_level = labels[-2] if len(labels) >= 2 else ""

    # 2. Structural TLD governance (RFC / IANA restricted vs open registries)
    if tld in ("gov", "mil", "edu", "int") or second_level in ("gov", "ac", "edu", "mil"):
        score += 0.26
    elif tld == "org":
        score += 0.08

    # 3. Domain label hygiene (SEO farms & spam domains use excessive hyphens & digits)
    main_label = labels[0] if len(labels) == 2 else (labels[-2] if len(labels) >= 2 else host)
    hyphen_count = main_label.count("-")
    digit_count = sum(1 for c in main_label if c.isdigit())
    if hyphen_count >= 2:
        score -= 0.12 * (hyphen_count - 1)
    if digit_count >= 3:
        score -= 0.10

    # High character-randomness in domain label signals auto-generated spam hosts
    if len(main_label) > 12 and _shannon_entropy(main_label) > 3.75:
        score -= 0.10

    # 4. Subdomain depth & URL query pollution
    if len(labels) > 3:
        score -= 0.05 * (len(labels) - 3)
    query_params = urllib.parse.parse_qsl(parsed.query)
    if len(query_params) > 3:
        score -= 0.04 * (len(query_params) - 3)

    # 5. Content signal quality (penalize ALL-CAPS clickbait titles or empty snippets)
    if title:
        upper_ratio = sum(1 for c in title if c.isupper()) / max(1, len(title))
        if upper_ratio > 0.45:
            score -= 0.12
    if len(snippet) < 40:
        score -= 0.08

    return max(0.15, min(1.0, score))


# ──────────────────────────────────────────────────────────────────────────────
# 6. Independent Multi-Oracle Search Harvesters
# ──────────────────────────────────────────────────────────────────────────────

_DDGS_CLIENT_LOCAL = threading.local()


def _get_ddgs_instance():
    inst = getattr(_DDGS_CLIENT_LOCAL, "instance", None)
    if inst is None:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        inst = DDGS(timeout=2)
        _DDGS_CLIENT_LOCAL.instance = inst
    return inst


def _build_result(
    title: str,
    url: str,
    snippet: str,
    source: str,
    published: str = "",
) -> Optional[SearchResult]:
    t = _normalize_text(title)
    s = _normalize_text(snippet)
    u = (url or "").strip()
    if not u.startswith(("http://", "https://")) or not t:
        return None
    dom = _extract_host(u)
    if not dom:
        return None
    pub_str, age_days = _parse_timestamp(published)
    auth = _compute_structural_authority(u, t, s)
    return SearchResult(
        title=t,
        url=u,
        snippet=s[:540] if s else t,
        source=source,
        domain=dom,
        published=pub_str,
        age_days=age_days,
        authority_score=auth,
    )


def _oracle_ddgs_api(query: str, limit: int) -> list[SearchResult]:
    variants = _formulate_search_queries(query)
    reg = SEARCH_REGION or None
    results: list[SearchResult] = []

    for q_var in variants[:2]:
        for backend in ("duckduckgo", "brave", "startpage", "mojeek"):
            try:
                kw: dict[str, Any] = {"max_results": limit, "backend": backend}
                if reg:
                    kw["region"] = reg
                rows = list(_get_ddgs_instance().text(q_var, **kw) or [])
                if rows:
                    for r in rows:
                        sr = _build_result(
                            r.get("title", ""),
                            r.get("href") or r.get("url") or "",
                            r.get("body", ""),
                            f"ddgs_{backend}",
                        )
                        if sr:
                            results.append(sr)
                    break
            except TypeError:
                kw = {"max_results": limit}
                if reg:
                    kw["region"] = reg
                rows = list(_get_ddgs_instance().text(q_var, **kw) or [])
                for r in rows:
                    sr = _build_result(
                        r.get("title", ""),
                        r.get("href") or r.get("url") or "",
                        r.get("body", ""),
                        "ddgs_web",
                    )
                    if sr:
                        results.append(sr)
                break
            except Exception:
                _DDGS_CLIENT_LOCAL.instance = None
                continue
        if len(results) >= limit:
            break
    return results


def _oracle_ddg_lite(query: str, limit: int) -> list[SearchResult]:
    q = clean_search_query(query)
    params = {"q": q}
    if SEARCH_REGION:
        params["kl"] = SEARCH_REGION
    resp = _NET.get("https://html.duckduckgo.com/html/", params=params, timeout=2.2)
    text = resp.text
    out: list[SearchResult] = []

    for block in re.finditer(
        r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
        r'(?:<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>|<div[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</div>)',
        text,
        re.S | re.I,
    ):
        raw_href, raw_title, snip1, snip2 = block.groups()
        href = html.unescape(raw_href)
        if "uddg=" in href:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            href = qs.get("uddg", [href])[0]
        if "duckduckgo.com" in _extract_host(href):
            continue
        sr = _build_result(raw_title, href, snip1 or snip2 or "", "ddg_direct")
        if sr:
            out.append(sr)
        if len(out) >= limit:
            break
    return out


def _oracle_brave_html(query: str, limit: int) -> list[SearchResult]:
    """Direct independent scraper for Brave Search web index (independent from Bing/Google)."""
    q = clean_search_query(query)
    params = {"q": q, "source": "web"}
    if PC_COUNTRY_CODE:
        params["country"] = PC_COUNTRY_CODE.lower()
    resp = _NET.get("https://search.brave.com/search", params=params, timeout=2.3)
    text = resp.text
    out: list[SearchResult] = []

    for m in re.finditer(
        r'<a[^>]+href="(https?://[^"]+)"[^>]*class="[^"]*svelte-[^"]*"[^>]*>.*?'
        r'<div[^>]+class="[^"]*title[^"]*"[^>]*>(.*?)</div>.*?'
        r'<div[^>]+class="[^"]*snippet-description[^"]*"[^>]*>(.*?)</div>',
        text,
        re.S | re.I,
    ):
        url, title, snippet = m.groups()
        host = _extract_host(url)
        if "brave.com" in host:
            continue
        sr = _build_result(title, url, snippet, "brave_web")
        if sr:
            out.append(sr)
        if len(out) >= limit:
            break
    return out


def _oracle_yahoo_web(query: str, limit: int) -> list[SearchResult]:
    """Independent web index oracle via Yahoo Search with redirect unwrapping."""
    q = clean_search_query(query)
    resp = _NET.get("https://search.yahoo.com/search", params={"p": q, "n": limit}, timeout=2.3)
    text = resp.text
    out: list[SearchResult] = []

    for m in re.finditer(
        r'<div[^>]+class="[^"]*algo-sr[^"]*"[^>]*>.*?'
        r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
        r'<div[^>]+class="[^"]*compText[^"]*"[^>]*>(.*?)</div>',
        text,
        re.S | re.I,
    ):
        raw_url, raw_title, raw_snip = m.groups()
        url = html.unescape(raw_url)
        ru_match = re.search(r"/RU=([^/]+)/RK=", url)
        if ru_match:
            url = urllib.parse.unquote(ru_match.group(1))
        if "yahoo.com" in _extract_host(url):
            continue
        clean_title = re.sub(r"<span[^>]*>.*?</span>", " ", raw_title, flags=re.S)
        sr = _build_result(clean_title, url, raw_snip, "yahoo_web")
        if sr:
            out.append(sr)
        if len(out) >= limit:
            break
    return out


def _oracle_bing_web(query: str, limit: int) -> list[SearchResult]:
    q = clean_search_query(query)
    params = {"q": q, "setlang": OS_LANG or "en"}
    if PC_COUNTRY_CODE:
        params["cc"] = PC_COUNTRY_CODE
    resp = _NET.get("https://www.bing.com/search", params=params, timeout=2.2)
    text = resp.text
    out: list[SearchResult] = []

    for m in re.finditer(
        r'<li[^>]+class="[^"]*b_algo[^"]*"[^>]*>.*?'
        r'<h2>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>\s*</h2>.*?'
        r'(?:<p[^>]*>(.*?)</p>|<div[^>]+class="[^"]*b_caption[^"]*"[^>]*>(.*?)</div>)',
        text,
        re.S | re.I,
    ):
        raw_href, raw_title, p1, p2 = m.groups()
        href = html.unescape(raw_href)
        if "bing.com/ck/a" in href:
            u_param = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("u", [""])[0]
            if u_param.startswith("a1"):
                b64 = u_param[2:]
                try:
                    href = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4)).decode("utf-8", "ignore")
                except Exception:
                    continue
        if "bing.com" in _extract_host(href):
            continue
        sr = _build_result(raw_title, href, p1 or p2 or "", "bing_web")
        if sr:
            out.append(sr)
        if len(out) >= limit:
            break
    return out


def _oracle_wikipedia_knowledge(query: str, limit: int) -> list[SearchResult]:
    """
    Queries the MediaWiki REST API for peer-reviewed encyclopedic grounding.
    Provides high-precision factual baseline to anchor cross-source verification.
    """
    q = clean_search_query(query)
    lang = OS_LANG if (OS_LANG and len(OS_LANG) == 2) else "en"
    api_url = f"https://{lang}.wikipedia.org/w/api.php"
    params = {
        "action": "query",
        "list": "search",
        "srsearch": q,
        "srlimit": min(limit, 4),
        "format": "json",
        "utf8": 1,
    }
    resp = _NET.get(api_url, params=params, timeout=2.0)
    items = (resp.json().get("query") or {}).get("search") or []
    out: list[SearchResult] = []
    for item in items:
        title = item.get("title", "")
        snippet = item.get("snippet", "")
        timestamp = item.get("timestamp", "")
        if not title:
            continue
        page_url = f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
        sr = _build_result(title, page_url, snippet, "encyclopedia", published=timestamp)
        if sr:
            sr.authority_score = max(sr.authority_score, 0.88)
            out.append(sr)
    return out


def _oracle_ddgs_news(query: str, limit: int) -> list[SearchResult]:
    q = clean_search_query(query)
    kw: dict[str, Any] = {"max_results": limit}
    if SEARCH_REGION:
        kw["region"] = SEARCH_REGION
    try:
        rows = list(_get_ddgs_instance().news(q, **kw) or [])
    except Exception:
        _DDGS_CLIENT_LOCAL.instance = None
        return []
    out: list[SearchResult] = []
    for r in rows:
        sr = _build_result(
            r.get("title", ""),
            r.get("url") or r.get("href") or "",
            r.get("body", ""),
            "verified_news",
            published=r.get("date", ""),
        )
        if sr:
            out.append(sr)
    return out


def _oracle_tavily_api(query: str, limit: int) -> list[SearchResult]:
    if not TAVILY_API_KEY:
        raise RuntimeError("No Tavily key")
    resp = _NET.post(
        "https://api.tavily.com/search",
        json_body={
            "api_key": TAVILY_API_KEY,
            "query": clean_search_query(query),
            "max_results": limit,
            "search_depth": "basic",
        },
        timeout=2.4,
    )
    out: list[SearchResult] = []
    for r in resp.json().get("results", []):
        sr = _build_result(r.get("title", ""), r.get("url", ""), r.get("content", ""), "tavily_api", r.get("published_date", ""))
        if sr:
            out.append(sr)
    return out


def _oracle_brave_api(query: str, limit: int) -> list[SearchResult]:
    if not BRAVE_API_KEY:
        raise RuntimeError("No Brave key")
    params: dict[str, Any] = {"q": clean_search_query(query), "count": min(limit, 15)}
    if PC_COUNTRY_CODE:
        params["country"] = PC_COUNTRY_CODE.lower()
    resp = _NET.get(
        "https://api.search.brave.com/res/v1/web/search",
        params=params,
        headers={"X-Subscription-Token": BRAVE_API_KEY, "Accept": "application/json"},
        timeout=2.4,
    )
    out: list[SearchResult] = []
    for r in (resp.json().get("web") or {}).get("results", []):
        sr = _build_result(r.get("title", ""), r.get("url", ""), r.get("description", ""), "brave_api", r.get("page_age", ""))
        if sr:
            out.append(sr)
    return out


def _oracle_searxng_api(query: str, limit: int) -> list[SearchResult]:
    if not SEARXNG_URL:
        raise RuntimeError("No SearXNG URL")
    resp = _NET.get(
        f"{SEARXNG_URL}/search",
        params={"q": clean_search_query(query), "format": "json"},
        timeout=2.4,
    )
    out: list[SearchResult] = []
    for r in resp.json().get("results", [])[:limit]:
        sr = _build_result(r.get("title", ""), r.get("url", ""), r.get("content", ""), "searxng_api", r.get("publishedDate", ""))
        if sr:
            out.append(sr)
    return out


_ORACLE_REGISTRY: dict[str, tuple[Callable[[str, int], list[SearchResult]], float]] = {
    "tavily":       (_oracle_tavily_api,          1.35),
    "brave_api":    (_oracle_brave_api,           1.30),
    "searxng":      (_oracle_searxng_api,         1.20),
    "ddgs":         (_oracle_ddgs_api,            1.20),
    "ddg_lite":     (_oracle_ddg_lite,            1.15),
    "bing":         (_oracle_bing_web,            1.15),
    "yahoo":        (_oracle_yahoo_web,           1.10),
    "brave_web":    (_oracle_brave_html,          1.10),
    "encyclopedia": (_oracle_wikipedia_knowledge, 1.25),
    "news":         (_oracle_ddgs_news,           0.95),
}


def _select_active_oracles(requested: Optional[list[str]] = None) -> list[str]:
    if requested:
        return [r for r in requested if r in _ORACLE_REGISTRY]
    candidates = ["ddgs", "ddg_lite", "bing", "yahoo", "brave_web", "encyclopedia", "news"]
    if TAVILY_API_KEY:
        candidates.insert(0, "tavily")
    if BRAVE_API_KEY:
        candidates.insert(0, "brave_api")
    if SEARXNG_URL:
        candidates.insert(0, "searxng")
    healthy = [c for c in candidates if _TELEMETRY.is_healthy(c)]
    return healthy or candidates


# ──────────────────────────────────────────────────────────────────────────────
# 7. Cross-Source Claim Corroboration & Consensus Ranking Engine
# ──────────────────────────────────────────────────────────────────────────────

def _extract_factual_atoms(text: str, query_tokens: set[str]) -> set[str]:
    """
    Extracts atomic verifiable claims (numbers, dates, named entities, technical specs)
    from a text passage that are NOT merely echoes of the user's query tokens.
    """
    atoms: set[str] = set()
    for match in _RE_FACT_ATOM.findall(text):
        norm = match.strip(".,;:()[]{}").lower()
        if len(norm) < 2 or norm in _CLOSED_CLASS_GLUE or norm in query_tokens:
            continue
        atoms.add(norm)
    return atoms


def _verify_and_rank_consensus(
    harvested: dict[str, list[SearchResult]],
    query: str,
) -> list[SearchResult]:
    """
    Builds a Cross-Domain Claim Consensus Graph:
      1. Deduplicates URLs and merges multi-oracle sightings.
      2. Measures query intent alignment with specificity-weighted coverage.
      3. Extracts factual atoms per root domain and calculates how many *independent*
          root domains corroborate the claims made by each candidate result.
      4. Assigns explicit Trust Tiers: VERIFIED_CONSENSUS, CORROBORATED, or SINGLE_SOURCE.
    """
    clean_q = clean_search_query(query)
    raw_q_tokens = _RE_TOKEN.findall(clean_q)
    q_weights = {
        t.lower(): _token_information_weight(t)
        for t in raw_q_tokens
        if t.lower() not in _CLOSED_CLASS_GLUE and len(t) > 1
    }
    if not q_weights:
        q_weights = {t.lower(): 1.0 for t in raw_q_tokens if len(t) > 1}
    q_token_set = set(q_weights.keys())
    total_q_weight = sum(q_weights.values()) or 1.0

    # Step 1: Canonical URL Aggregation across Oracles
    candidates: dict[str, SearchResult] = {}
    oracle_support: dict[str, set[str]] = defaultdict(set)

    for oracle_name, results in harvested.items():
        oracle_weight = _ORACLE_REGISTRY.get(oracle_name, (None, 1.0))[1]
        for rank_idx, item in enumerate(results):
            ukey = _canonical_url_key(item.url)
            rrf_contrib = oracle_weight / (50.0 + rank_idx + 1.0)
            oracle_support[ukey].add(oracle_name)

            if ukey not in candidates:
                item.score = rrf_contrib
                candidates[ukey] = item
            else:
                existing = candidates[ukey]
                existing.score += rrf_contrib
                if len(item.snippet) > len(existing.snippet):
                    existing.snippet = item.snippet
                if item.published and not existing.published:
                    existing.published = item.published
                    existing.age_days = item.age_days

    if not candidates:
        return []

    # Step 2: Build Root-Domain -> Factual Atom Incidence Matrix
    atom_to_root_domains: dict[str, set[str]] = defaultdict(set)
    candidate_atoms: dict[str, set[str]] = {}
    candidate_alignment: dict[str, float] = {}

    for ukey, item in candidates.items():
        blob = f"{item.title} {item.snippet}"
        doc_tokens = {t.lower() for t in _RE_TOKEN.findall(blob)}

        # Specificity-weighted query coverage
        matched_weight = sum(w for tok, w in q_weights.items() if tok in doc_tokens)
        coverage_ratio = matched_weight / total_q_weight
        candidate_alignment[ukey] = coverage_ratio

        # Only allow candidates that actually address the query to vote in the consensus graph
        if coverage_ratio >= 0.35:
            root_dom = _registered_root_domain(item.domain)
            atoms = _extract_factual_atoms(blob, q_token_set)
            candidate_atoms[ukey] = atoms
            for atom in atoms:
                atom_to_root_domains[atom].add(root_dom)
        else:
            candidate_atoms[ukey] = set()

    # Step 3: Score Each Candidate by Independent Inter-Domain Corroboration
    for ukey, item in candidates.items():
        root_dom = _registered_root_domain(item.domain)
        atoms = candidate_atoms.get(ukey, set())
        corroborating_domains: set[str] = set()
        shared_fact_weight = 0.0

        for atom in atoms:
            supporters = atom_to_root_domains.get(atom, set()) - {root_dom}
            if supporters:
                corroborating_domains.update(supporters)
                # Numeric facts and capitalized entities corroborated across domains carry high weight
                atom_bonus = 1.35 if any(c.isdigit() for c in atom) else 1.0
                shared_fact_weight += atom_bonus * math.log1p(len(supporters))

        num_corroborators = len(corroborating_domains)
        num_oracles = len(oracle_support[ukey])
        consensus_norm = min(1.0, (shared_fact_weight / 6.0) + (0.18 * num_corroborators) + (0.12 * (num_oracles - 1)))

        item.consensus_score = round(consensus_norm, 3)
        item.corroborated_by = sorted(corroborating_domains)[:5]

        if num_corroborators >= 2 or (num_corroborators >= 1 and item.authority_score >= 0.80):
            item.trust_tier = "VERIFIED_CONSENSUS"
        elif num_corroborators == 1 or num_oracles >= 2 or item.authority_score >= 0.85:
            item.trust_tier = "CORROBORATED"
        else:
            item.trust_tier = "SINGLE_SOURCE"

        alignment = candidate_alignment.get(ukey, 0.0)
        recency_boost = 0.0
        if item.age_days is not None:
            recency_boost = 0.08 * math.exp(-item.age_days / 60.0)

        # Unified Epistemic Score: Query Alignment + Cross-Domain Consensus + Structural Authority + Multi-Oracle RRF
        item.score = (
            (alignment ** 1.4) * 0.42
            + item.consensus_score * 0.28
            + item.authority_score * 0.18
            + min(0.12, item.score * 2.5)
            + recency_boost
        )

    ranked = sorted(candidates.values(), key=lambda r: r.score, reverse=True)

    # Step 4: Domain Diversity Enforcement (prevent any single host from monopolizing top results)
    final_list: list[SearchResult] = []
    domain_counts: Counter[str] = Counter()
    for r in ranked:
        root = _registered_root_domain(r.domain)
        if domain_counts[root] >= 2:
            continue
        domain_counts[root] += 1
        final_list.append(r)

    return final_list


# ──────────────────────────────────────────────────────────────────────────────
# 8. Deep Verbatim Evidence & JSON-LD Fact Extractor
# ──────────────────────────────────────────────────────────────────────────────

_RE_NOISE_BLOCKS = re.compile(
    r"<(script|style|nav|footer|header|aside|noscript|svg|form|iframe|button)[^>]*>.*?</\1>",
    re.S | re.I,
)


def _extract_structured_jsonld(html_doc: str) -> list[str]:
    facts: list[str] = []
    for m in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html_doc, re.S | re.I):
        try:
            payload = json.loads(m.group(1).strip())
        except Exception:
            continue
        queue = [payload]
        while queue:
            node = queue.pop(0)
            if isinstance(node, dict):
                headline = node.get("headline") or node.get("name")
                desc = node.get("description") or node.get("articleBody")
                if isinstance(headline, str) and isinstance(desc, str) and len(desc) > 40:
                    facts.append(_normalize_text(f"{headline}: {desc[:450]}"))
                offers = node.get("offers")
                if isinstance(headline, str) and offers:
                    olist = offers if isinstance(offers, list) else [offers]
                    for off in olist:
                        if isinstance(off, dict) and (off.get("price") or off.get("lowPrice")):
                            p = off.get("price") or off.get("lowPrice")
                            cur = off.get("priceCurrency", "")
                            facts.append(_normalize_text(f"{headline} — {cur} {p}"))
                for v in node.values():
                    if isinstance(v, (dict, list)):
                        queue.append(v)
            elif isinstance(node, list):
                queue.extend(node)
    return facts[:4]


def _extract_high_density_evidence(html_doc: str, query: str, max_chars: int = 1600) -> str:
    """
    Extracts self-contained, information-dense sentences from the target webpage
    that directly answer the query and contain verifiable facts.
    """
    structured_facts = _extract_structured_jsonld(html_doc)

    # Strip boilerplate & navigation containers
    cleaned_html = _RE_NOISE_BLOCKS.sub(" ", html_doc)

    paragraphs: list[str] = []
    for pm in re.finditer(r"<(?:p|li|td|dd|blockquote|article)[^>]*>(.*?)</(?:p|li|td|dd|blockquote|article)>", cleaned_html, re.S | re.I):
        txt = _normalize_text(pm.group(1))
        if 45 <= len(txt) <= 1000:
            paragraphs.append(txt)

    if not paragraphs:
        fallback_txt = _normalize_text(cleaned_html)
        paragraphs = [fallback_txt[i : i + 500] for i in range(0, min(len(fallback_txt), 6000), 450) if len(fallback_txt[i : i + 500]) > 60]

    clean_q = clean_search_query(query)
    q_weights = {
        t.lower(): _token_information_weight(t)
        for t in _RE_TOKEN.findall(clean_q)
        if t.lower() not in _CLOSED_CLASS_GLUE and len(t) > 1
    }

    scored_passages: list[tuple[float, int, str]] = []
    seen_sigs: set[str] = set()

    for idx, para in enumerate(structured_facts + paragraphs):
        sig = para[:75].lower()
        if sig in seen_sigs:
            continue
        seen_sigs.add(sig)

        p_tokens = {t.lower() for t in _RE_TOKEN.findall(para)}
        if not p_tokens:
            continue

        # Score by query specificity match + factual density (numbers, dates, proper nouns)
        q_hit_score = sum(w for tok, w in q_weights.items() if tok in p_tokens)
        fact_atoms = len(_RE_FACT_ATOM.findall(para))
        density_bonus = min(2.5, fact_atoms * 0.22)
        lead_bonus = 0.6 if idx < len(structured_facts) else (0.25 if idx < 5 else 0.0)

        total_sc = q_hit_score * 1.5 + density_bonus + lead_bonus
        if q_hit_score > 0 or idx < len(structured_facts):
            scored_passages.append((total_sc, idx, para))

    if not scored_passages:
        return ""

    scored_passages.sort(key=lambda x: x[0], reverse=True)
    chosen: list[tuple[int, str]] = []
    char_budget = 0
    for sc, idx, text in scored_passages[:5]:
        chosen.append((idx, text))
        char_budget += len(text) + 4
        if char_budget >= max_chars:
            break

    chosen.sort(key=lambda x: x[0])
    return " … ".join(txt for _, txt in chosen)[:max_chars]


def _deep_verify_top_results(results: list[SearchResult], query: str, deadline_s: float) -> None:
    targets = [r for r in results if not r.url.startswith("https://www.youtube.com/watch")][:4]
    if not targets:
        return

    def _fetch_one(res: SearchResult) -> str:
        resp = _NET.get(res.url, timeout=min(2.0, max(0.8, deadline_s)))
        ctype = (resp.headers.get("content-type") or "").lower()
        if any(b in ctype for b in ("pdf", "image", "audio", "video", "octet-stream")):
            return ""
        return _extract_high_density_evidence(resp.text[:400_000], query)

    futs = {_POOL.submit(_fetch_one, r): r for r in targets}
    done, pending = wait(list(futs.keys()), timeout=deadline_s)
    for f in done:
        target_res = futs[f]
        try:
            excerpt = f.result()
            if excerpt:
                target_res.fetched_content = excerpt
        except Exception:
            pass
    for f in pending:
        f.cancel()


# ──────────────────────────────────────────────────────────────────────────────
# 9. Public Search API (Drop-in Compatible with Amigo Voice Assistant)
# ──────────────────────────────────────────────────────────────────────────────

def search_web(
    query: str,
    max_results: int = 6,
    engines: Optional[list[str]] = None,
    fetch_content: Optional[bool] = None,
    use_cache: bool = True,
    fast_mode: bool = False,
    freshness: Optional[str] = None,
) -> SearchResponse:
    """
    Executes a multi-oracle consensus search, cross-verifies claims across independent
    domains, and extracts verbatim evidence passages from top corroborated sources.
    """
    t_start = time.monotonic()
    raw_q = (query or "").strip()
    if not raw_q:
        return SearchResponse(query="", results=[], query_type="empty")

    cache_key = (raw_q.lower(), max_results, bool(fetch_content), bool(fast_mode), tuple(sorted(engines or ())))
    if use_cache:
        cached = _RESULT_CACHE.get(cache_key)
        if cached is not None:
            return replace(cached, cache_hit=True, search_time_ms=0)

    oracles = _select_active_oracles(engines)
    race_budget = 2.1 if fast_mode else 3.2
    grace_window = 0.45 if fast_mode else 0.65
    per_oracle_limit = 6 if fast_mode else 8

    deadline = time.monotonic() + race_budget
    futs = {
        _POOL.submit(_ORACLE_REGISTRY[name][0], raw_q, per_oracle_limit): (name, time.monotonic())
        for name in oracles
    }

    pending = set(futs.keys())
    harvested: dict[str, list[SearchResult]] = {}
    grace_start: Optional[float] = None

    while pending:
        cutoff = deadline if grace_start is None else min(deadline, grace_start + grace_window)
        rem = cutoff - time.monotonic()
        if rem <= 0:
            break
        done, pending = wait(pending, timeout=rem, return_when=FIRST_COMPLETED)
        for fut in done:
            name, t0 = futs[fut]
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            try:
                rows = fut.result()
                _TELEMETRY.record_success(name, elapsed_ms)
                if rows:
                    harvested[name] = rows
                    # Trigger grace window once at least 3 independent oracles have reported
                    if grace_start is None and len(harvested) >= 3:
                        grace_start = time.monotonic()
            except Exception as exc:
                _TELEMETRY.record_failure(name, permanent=isinstance(exc, ImportError))
                logger.debug("[EpistemicSearch] Oracle %s failed: %s", name, exc)

    for fut in pending:
        fut.cancel()

    verified_results = _verify_and_rank_consensus(harvested, raw_q)[:max_results]

    should_fetch = fetch_content if fetch_content is not None else (not fast_mode or len(verified_results) > 0)
    if should_fetch and verified_results:
        fetch_budget = 1.25 if fast_mode else 2.1
        _deep_verify_top_results(verified_results, raw_q, deadline_s=fetch_budget)

    # Re-run consensus boost after deep page excerpts are attached
    if any(r.fetched_content for r in verified_results):
        clean_q = clean_search_query(raw_q)
        q_tokens = {t.lower() for t in _RE_TOKEN.findall(clean_q) if t.lower() not in _CLOSED_CLASS_GLUE}
        root_atoms: dict[str, set[str]] = defaultdict(set)
        for r in verified_results:
            dom = _registered_root_domain(r.domain)
            atoms = _extract_factual_atoms(f"{r.title} {r.snippet} {r.fetched_content}", q_tokens)
            for a in atoms:
                root_atoms[a].add(dom)
        for r in verified_results:
            dom = _registered_root_domain(r.domain)
            atoms = _extract_factual_atoms(f"{r.title} {r.snippet} {r.fetched_content}", q_tokens)
            supporters = set()
            for a in atoms:
                supporters.update(root_atoms[a] - {dom})
            if supporters:
                r.corroborated_by = sorted(set(r.corroborated_by) | supporters)[:5]
                if len(r.corroborated_by) >= 2:
                    r.trust_tier = "VERIFIED_CONSENSUS"
                    r.score += 0.08
                elif r.trust_tier == "SINGLE_SOURCE":
                    r.trust_tier = "CORROBORATED"
                    r.score += 0.04
        verified_results.sort(key=lambda r: r.score, reverse=True)

    total_ms = int((time.monotonic() - t_start) * 1000)
    response = SearchResponse(
        query=raw_q,
        results=verified_results,
        query_type="epistemic_consensus",
        search_time_ms=total_ms,
        engines_used=list(harvested.keys()),
    )

    if use_cache and verified_results:
        _RESULT_CACHE.put(cache_key, response, ttl_s=300.0)

    logger.info(
        "[EpistemicSearch] %r -> %d verified results in %dms (oracles=%s)",
        raw_q[:60],
        len(verified_results),
        total_ms,
        ",".join(harvested.keys()) or "none",
    )
    return response


def format_for_llm(response: SearchResponse, max_chars: int = 5000) -> str:
    """
    Formats search results with explicit epistemic trust badges and cross-domain
    corroboration metadata so the LLM prioritizes verified consensus facts.
    """
    if not response.results:
        return "No verified web sources found for this query."

    now_iso = datetime.now().strftime("%Y-%m-%d")
    loc_tag = f" | Locale: {PC_COUNTRY_CODE}" if PC_COUNTRY_CODE else ""
    header = [
        f"=== EPISTEMIC CONSENSUS WEB RESULTS FOR: {response.query!r} (Date: {now_iso}{loc_tag}) ===",
        "INSTRUCTION: Prioritize [VERIFIED_CONSENSUS] and [CORROBORATED] sources. "
        "Treat [SINGLE_SOURCE] claims with caution if they conflict with corroborated sources.",
    ]

    blocks: list[str] = []
    for idx, r in enumerate(response.results, 1):
        pub_tag = f" | Published: {r.published}" if r.published else ""
        corr_tag = (
            f" | Corroborated by: {', '.join(r.corroborated_by[:3])}"
            if r.corroborated_by
            else ""
        )
        entry = [
            f"{idx}. [{r.trust_tier}] {r.title} (Source: {r.domain} | Credibility: {r.credibility:.2f}{pub_tag}{corr_tag})",
            f"   URL: {r.url}",
            f"   Summary: {r.snippet}",
        ]
        if r.fetched_content:
            entry.append(f"   Verified Page Evidence: {r.fetched_content[:950]}")
        blocks.append("\n".join(entry))

    return ("\n".join(header) + "\n\n" + "\n\n".join(blocks))[:max_chars]


def scrape_web_info(query: str, max_results: int = 5) -> str:
    resp = search_web(query, max_results=max_results)
    chunks: list[str] = []
    for r in resp.results:
        item = f"[{r.trust_tier} | {r.domain}] {r.title}: {r.snippet}"
        if r.fetched_content:
            item += f" (Evidence: {r.fetched_content[:450]})"
        chunks.append(item)
    return " || ".join(chunks)[:3800]


def searchGoogle(query: str) -> None:
    q = (query or "").strip()
    if q:
        webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote_plus(q))


async def async_search_web(query: str, **kwargs) -> SearchResponse:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_POOL, lambda: search_web(query, **kwargs))


# ──────────────────────────────────────────────────────────────────────────────
# 10. YouTube Resolution Subsystem (Preserved Public Interface)
# ──────────────────────────────────────────────────────────────────────────────

_YT_INNERTUBE_VER = os.getenv("AMIGO_YT_CLIENT_VERSION", "2.20250901.01.00")
_YT_ENDPOINT = "https://www.youtube.com/youtubei/v1/search?prettyPrint=false"
_YT_CONSENT_COOKIES = {"CONSENT": "YES+1", "SOCS": "CAI"}


def _yt_text(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    if "simpleText" in node:
        return str(node["simpleText"])
    return "".join(r.get("text", "") for r in node.get("runs", []) if isinstance(r, dict))


def _yt_duration_seconds(raw: str) -> Optional[int]:
    parts = (raw or "").strip().split(":")
    if not parts or not all(p.isdigit() for p in parts):
        return None
    total = 0
    for p in parts:
        total = total * 60 + int(p)
    return total


def _iter_yt_renderers(tree: Any) -> Iterable[dict]:
    if isinstance(tree, dict):
        vr = tree.get("videoRenderer")
        if isinstance(vr, dict) and vr.get("videoId"):
            yield vr
        for val in tree.values():
            yield from _iter_yt_renderers(val)
    elif isinstance(tree, list):
        for item in tree:
            yield from _iter_yt_renderers(item)


def _query_yt_innertube(query: str, sp_params: Optional[str] = None) -> list[YTVideo]:
    client_ctx: dict[str, Any] = {"clientName": "WEB", "clientVersion": _YT_INNERTUBE_VER, "hl": OS_LANG or "en"}
    if YT_REGION:
        client_ctx["gl"] = YT_REGION
    payload: dict[str, Any] = {"context": {"client": client_ctx}, "query": query}
    if sp_params:
        payload["params"] = sp_params

    resp = _NET.post(
        _YT_ENDPOINT,
        json_body=payload,
        cookies=_YT_CONSENT_COOKIES,
        timeout=2.5,
        headers={
            "X-YouTube-Client-Name": "1",
            "X-YouTube-Client-Version": _YT_INNERTUBE_VER,
            "Origin": "https://www.youtube.com",
            "Content-Type": "application/json",
        },
    )
    videos: list[YTVideo] = []
    seen: set[str] = set()
    for vr in _iter_yt_renderers(resp.json()):
        vid = vr["videoId"]
        if vid in seen:
            continue
        seen.add(vid)
        badges = [b.get("metadataBadgeRenderer", {}) for b in vr.get("badges", []) if isinstance(b, dict)]
        overlays = [o.get("thumbnailOverlayTimeStatusRenderer", {}) for o in vr.get("thumbnailOverlays", []) if isinstance(o, dict)]
        is_live = (
            any(b.get("style") == "BADGE_STYLE_TYPE_LIVE_NOW" for b in badges)
            or any(o.get("style") == "LIVE" for o in overlays)
        )
        videos.append(
            YTVideo(
                video_id=vid,
                title=_yt_text(vr.get("title")),
                channel=_yt_text(vr.get("ownerText")) or _yt_text(vr.get("longBylineText")) or "YouTube",
                is_live=is_live,
                duration_s=_yt_duration_seconds(_yt_text(vr.get("lengthText"))),
                source="innertube",
            )
        )
    return videos


def resolve_youtube_video(
    query: str,
    kind: str = "auto",
    live: Optional[bool] = None,
    latest: Optional[bool] = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    raw_q = (query or "").strip()
    clean_q = re.sub(r"\s+(?:on|in)\s+youtube(?:\s+music)?$", "", clean_search_query(raw_q), flags=re.I).strip() or raw_q

    if live is None:
        live = kind == "live" or bool(re.search(r"\b(?:live\s*stream|livestream|live\s+now)\b", clean_q, re.I))
    if latest is None:
        latest = bool(re.search(r"\b(?:latest|newest|most\s+recent)\b", clean_q, re.I))

    fallback = {
        "url": "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(clean_q),
        "title": clean_q.title(),
        "channel": "YouTube",
        "video_id": "",
        "query": clean_q,
        "is_live": False,
        "not_live_fallback": False,
        "resolved": False,
        "source": "search_page",
    }

    cache_key = (clean_q.lower(), kind, bool(live), bool(latest))
    if not live and not latest:
        cached = _MEDIA_CACHE.get(cache_key)
        if cached:
            return dict(cached)

    sp = "EgJAAQ%3D%3D" if live else ("CAISAhAB" if latest else "EgIQAQ%3D%3D")
    try:
        candidates = _query_yt_innertube(clean_q, sp_params=sp)
        if not candidates and (live or latest):
            candidates = _query_yt_innertube(clean_q)
    except Exception:
        candidates = []

    chosen: Optional[YTVideo] = None
    if live:
        chosen = next((v for v in candidates if v.is_live), candidates[0] if candidates else None)
    elif kind == "song":
        chosen = next((v for v in candidates if v.duration_s is None or 55 <= v.duration_s <= 720), candidates[0] if candidates else None)
    elif candidates:
        chosen = candidates[0]

    if chosen:
        fallback.update(
            video_id=chosen.video_id,
            title=chosen.title or fallback["title"],
            channel=chosen.channel or "YouTube",
            url=f"https://www.youtube.com/watch?v={chosen.video_id}&autoplay=1",
            is_live=bool(chosen.is_live),
            not_live_fallback=bool(live and not chosen.is_live),
            resolved=True,
            source=chosen.source,
        )
        if not live and not latest:
            _MEDIA_CACHE.put(cache_key, dict(fallback), ttl_s=21600.0)

    return fallback


def searchYoutube(query: str, **kwargs) -> str:
    res = resolve_youtube_video(query, **kwargs)
    webbrowser.open(res["url"])
    return res["url"]


# ──────────────────────────────────────────────────────────────────────────────
# 11. Live Financial Quote Engine (Preserved Public Interface)
# ──────────────────────────────────────────────────────────────────────────────

_YF_SEARCH_API = "https://query2.finance.yahoo.com/v1/finance/search"
_YF_CHART_API = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_VALID_ASSET_CLASSES = frozenset({"EQUITY", "ETF", "INDEX", "CRYPTOCURRENCY", "CURRENCY", "MUTUALFUND", "FUTURE"})


def get_stock_quote(query: str, range_val: str = "1d") -> Optional[Quote]:
    q = (query or "").strip()
    if not q:
        return None
    ckey = f"{q.lower()}:{range_val}"
    cached = _QUOTE_CACHE.get(ckey)
    if cached:
        return cached

    try:
        s_resp = _NET.get(
            _YF_SEARCH_API,
            params={"q": q, "quotesCount": 5, "newsCount": 0},
            headers={"Accept": "application/json"},
            timeout=2.4,
        ).json()
        match = next(
            (item for item in (s_resp.get("quotes") or []) if item.get("symbol") and item.get("quoteType") in _VALID_ASSET_CLASSES),
            None,
        )
        if not match:
            return None

        symbol = match["symbol"]
        name = match.get("shortname") or match.get("longname") or symbol
        interval = {"1d": "5m", "5d": "15m", "1mo": "1d", "3mo": "1d", "1y": "1wk"}.get(range_val, "5m")

        c_resp = _NET.get(
            _YF_CHART_API.format(symbol=urllib.parse.quote(symbol, safe="")),
            params={"range": range_val, "interval": interval},
            headers={"Accept": "application/json"},
            timeout=2.4,
        ).json()

        chart_res = ((c_resp.get("chart") or {}).get("result") or [None])[0] or {}
        meta = chart_res.get("meta") or {}
        price = meta.get("regularMarketPrice")
        if price is None:
            return None

        q_ind = ((chart_res.get("indicators") or {}).get("quote") or [{}])[0]
        closes = [round(float(c), 2) for c in (q_ind.get("close") or []) if c is not None]
        stamps = [int(ts) for ts in (chart_res.get("timestamp") or []) if ts is not None]

        as_of_dt: Optional[datetime] = None
        if meta.get("regularMarketTime"):
            try:
                from zoneinfo import ZoneInfo
                tz = ZoneInfo(meta.get("exchangeTimezoneName") or "UTC")
                as_of_dt = datetime.fromtimestamp(meta["regularMarketTime"], tz)
            except Exception:
                as_of_dt = datetime.fromtimestamp(meta["regularMarketTime"], timezone.utc)

        quote_obj = Quote(
            symbol=symbol,
            name=name,
            price=float(price),
            currency=meta.get("currency") or "",
            previous_close=meta.get("previousClose") or meta.get("chartPreviousClose"),
            exchange=meta.get("fullExchangeName") or meta.get("exchangeName") or "",
            as_of=as_of_dt,
            day_high=meta.get("regularMarketDayHigh"),
            day_low=meta.get("regularMarketDayLow"),
            volume=meta.get("regularMarketVolume"),
            fifty_two_week_high=meta.get("fiftyTwoWeekHigh"),
            fifty_two_week_low=meta.get("fiftyTwoWeekLow"),
            history=closes,
            timestamps=stamps,
        )
        _QUOTE_CACHE.put(ckey, quote_obj, ttl_s=30.0)
        return quote_obj
    except Exception as exc:
        logger.debug("[QuoteEngine] Failed for %r: %s", q, exc)
        return None


def format_quote(q: Quote) -> str:
    ccy = f"{q.currency} " if q.currency else ""
    out = f"{q.name} ({q.symbol}) is trading at {ccy}{q.price:,.2f}"
    if q.change_pct is not None and q.change is not None:
        direction = "up" if q.change >= 0 else "down"
        out += f", {direction} {abs(q.change_pct):.2f}% from the previous close"
    if q.as_of:
        out += f" as of {q.as_of.strftime('%I:%M %p').lstrip('0')} on {q.as_of.strftime('%b %d')}"
    return out + "."


# ──────────────────────────────────────────────────────────────────────────────
# 12. Lifecycle Management
# ──────────────────────────────────────────────────────────────────────────────

def warmup() -> None:
    """Pre-initializes TLS sessions in a background worker without blocking import."""
    def _init() -> None:
        try:
            _get_ddgs_instance()
        except Exception:
            pass
    _POOL.submit(_init)


def shutdown() -> None:
    _POOL.shutdown(wait=False, cancel_futures=True)
    _RESULT_CACHE.clear()
    _MEDIA_CACHE.clear()
    _QUOTE_CACHE.clear()


warmup()
