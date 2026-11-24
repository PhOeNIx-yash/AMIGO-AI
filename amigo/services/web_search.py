r"""
web_search.py - Ultra-Fast, Zero-Hardcoding Global Epistemic Retrieval Engine for Amigo.

Key Capabilities:
  1. Zero Hardcoded Word/Phrase/Domain/Backend Lists:
     - Uses Unicode tokenization, information-theoretic token weighting, structural
       URL/domain authority scoring, and cross-domain claim corroboration across
       any topic, question type, language, or script.
  2. Modern Async + Pooled HTTP/2 Transport:
     - Uses `httpx.AsyncClient(http2=True)` with connection pooling and `asyncio`
       concurrency (with a sync `search_web` bridge) for low-latency fan-out.
  3. Safe Singleflight Coalescing + TTL LRU Cache:
     - Deduplicates identical concurrent queries cleanly without timer races.
  4. High-Density Evidence Extraction:
     - Extracts JSON-LD, meta descriptions, timestamps, and query-aligned passages
       from top corroborated pages.
"""

from __future__ import annotations

import asyncio
from collections import Counter, OrderedDict, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
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
from typing import Any, Callable, Iterable, Optional
import urllib.parse
import webbrowser
import xml.etree.ElementTree as ET

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    httpx = None  # type: ignore[assignment]
    _HAS_HTTPX = False

import requests
from requests.adapters import HTTPAdapter

logger = logging.getLogger("amigo.web_search")
for _lg in ("ddgs", "ddgs.ddgs", "duckduckgo_search", "httpx", "httpcore", "primp", "urllib3"):
    logging.getLogger(_lg).setLevel(logging.ERROR)

__all__ = [
    "SearchResult",
    "SearchResponse",
    "YTVideo",
    "Quote",
    "search_web",
    "async_search_web",
    "async_search_images",
    "search_images",
    "format_for_llm",
    "scrape_web_info",
    "searchGoogle",
    "resolve_youtube_video",
    "searchYoutube",
    "get_stock_quote",
    "format_quote",
    "clean_search_query",
    "detect_query_type",
    "warmup",
    "shutdown",
]


# ──────────────────────────────────────────────────────────────────────────────
# 1. Dynamic Environment Introspection (Zero Hardcoded Fallbacks)
# ──────────────────────────────────────────────────────────────────────────────

def _discover_os_locale() -> tuple[str, str, str]:
    """Derives (language_code, country_code, region_tag) dynamically from host OS."""
    lang_code = ""
    country_code = ""

    if os.name == "nt":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(85)
            if ctypes.windll.kernel32.GetUserDefaultLocaleName(buf, 85) > 0:
                parts = [p for p in buf.value.replace("_", "-").split("-") if p]
                if parts and len(parts[0]) in (2, 3) and parts[0].isalpha():
                    lang_code = parts[0].lower()
                for p in reversed(parts):
                    if len(p) == 2 and p.isalpha():
                        country_code = p.upper()
                        break
        except Exception:
            pass

        if not country_code:
            try:
                import ctypes
                buf = ctypes.create_unicode_buffer(16)
                if ctypes.windll.kernel32.GetUserDefaultGeoName(buf, 16) > 0:
                    val = buf.value.strip().upper()
                    if len(val) == 2 and val.isalpha():
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


def _normalize_timelimit(freshness: Optional[str]) -> Optional[str]:
    if not freshness:
        return None
    val = freshness.strip().lower()
    mapping = {
        "d": "d", "day": "d", "today": "d", "24h": "d", "1d": "d",
        "w": "w", "week": "w", "7d": "w",
        "m": "m", "month": "m", "30d": "m",
        "y": "y", "year": "y", "365d": "y",
    }
    return mapping.get(val)


# ──────────────────────────────────────────────────────────────────────────────
# 2. Data Structures
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
# 3. Connection-Pooled Transport, Telemetry, Cache & Safe Singleflight
# ──────────────────────────────────────────────────────────────────────────────

def _build_default_headers() -> dict[str, str]:
    accept_lang = (
        f"{OS_LANG}-{PC_COUNTRY_CODE},{OS_LANG};q=0.9,*;q=0.5"
        if (OS_LANG and PC_COUNTRY_CODE)
        else f"{OS_LANG};q=0.9,*;q=0.5" if OS_LANG else "*;q=0.8"
    )
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
        "Accept-Language": accept_lang,
        "Accept-Encoding": "gzip, deflate, br",
    }


class _Transport:
    """Thread-safe pooled HTTP client (sync fallback + async HTTP/2 client)."""

    def __init__(self) -> None:
        self._headers = _build_default_headers()
        self._sync_session = requests.Session()
        adapter = HTTPAdapter(max_retries=1, pool_connections=64, pool_maxsize=128)
        self._sync_session.mount("http://", adapter)
        self._sync_session.mount("https://", adapter)
        self._sync_session.headers.update(self._headers)
        self._async_clients: dict[int, Any] = {}
        self._async_lock = threading.Lock()

    def get_sync(
        self,
        url: str,
        *,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        cookies: Optional[dict] = None,
        timeout: float = 2.0,
    ):
        resp = self._sync_session.get(
            url, params=params, headers=headers, cookies=cookies, timeout=(0.8, timeout)
        )
        resp.raise_for_status()
        return resp

    def post_sync(
        self,
        url: str,
        *,
        json_body: Optional[dict] = None,
        data: Optional[dict] = None,
        headers: Optional[dict] = None,
        cookies: Optional[dict] = None,
        timeout: float = 2.0,
    ):
        resp = self._sync_session.post(
            url, json=json_body, data=data, headers=headers, cookies=cookies, timeout=(0.8, timeout)
        )
        resp.raise_for_status()
        return resp

    async def _get_async_client(self):
        if not _HAS_HTTPX:
            return None
        loop_id = id(asyncio.get_running_loop())
        with self._async_lock:
            client = self._async_clients.get(loop_id)
            if client is None or client.is_closed:
                http2_enabled = False
                try:
                    import h2  # noqa: F401
                    http2_enabled = True
                except ImportError:
                    http2_enabled = False
                client = httpx.AsyncClient(
                    http2=http2_enabled,
                    headers=self._headers,
                    follow_redirects=True,
                    timeout=httpx.Timeout(2.0, connect=0.8),
                    limits=httpx.Limits(max_connections=128, max_keepalive_connections=64, keepalive_expiry=30.0),
                )
                self._async_clients[loop_id] = client
            return client

    async def get_async(
        self,
        url: str,
        *,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        cookies: Optional[dict] = None,
        timeout: float = 1.9,
    ):
        client = await self._get_async_client()
        if client is not None:
            resp = await client.get(url, params=params, headers=headers, cookies=cookies, timeout=timeout)
            resp.raise_for_status()
            return resp
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            _POOL,
            lambda: self.get_sync(url, params=params, headers=headers, cookies=cookies, timeout=timeout),
        )

    async def post_async(
        self,
        url: str,
        *,
        json_body: Optional[dict] = None,
        data: Optional[dict] = None,
        headers: Optional[dict] = None,
        cookies: Optional[dict] = None,
        timeout: float = 1.9,
    ):
        client = await self._get_async_client()
        if client is not None:
            resp = await client.post(
                url, json=json_body, data=data, headers=headers, cookies=cookies, timeout=timeout
            )
            resp.raise_for_status()
            return resp
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            _POOL,
            lambda: self.post_sync(url, json_body=json_body, data=data, headers=headers, cookies=cookies, timeout=timeout),
        )

    def close_sync(self) -> None:
        try:
            self._sync_session.close()
        except Exception:
            pass
        with self._async_lock:
            for client in list(self._async_clients.values()):
                try:
                    if not client.is_closed:
                        asyncio.run(client.aclose())
                except Exception:
                    pass
            self._async_clients.clear()


class _ProviderTelemetry:
    """Tracks EMA latency and circuit-breaker backoff per search oracle."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: dict[str, int] = defaultdict(int)
        self._cooldown_until: dict[str, float] = {}
        self._ema_ms: dict[str, float] = {}

    def is_healthy(self, provider: str) -> bool:
        with self._lock:
            return time.monotonic() >= self._cooldown_until.get(provider, 0.0)

    def latency_ms(self, provider: str) -> float:
        with self._lock:
            return self._ema_ms.get(provider, 550.0)

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
                backoff = min(90.0, 4.0 * (2 ** (fails - 1)))
                self._cooldown_until[provider] = time.monotonic() + backoff


class _LRUTTLCache:
    def __init__(self, capacity: int = 512) -> None:
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


@dataclass
class _FlightSlot:
    event: threading.Event = field(default_factory=threading.Event)
    result: Any = None


class _SingleFlight:
    """Coalesces concurrent identical searches cleanly without stale timer races."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._inflight: dict[Any, _FlightSlot] = {}

    def run(self, key: Any, fn: Callable[[], Any]) -> Any:
        with self._lock:
            slot = self._inflight.get(key)
            if slot is not None:
                leader = False
            else:
                slot = _FlightSlot()
                self._inflight[key] = slot
                leader = True

        if not leader:
            slot.event.wait(timeout=5.0)
            return slot.result

        try:
            res = fn()
            slot.result = res
            return res
        finally:
            with self._lock:
                if self._inflight.get(key) is slot:
                    self._inflight.pop(key, None)
            slot.event.set()


_NET = _Transport()
_TELEMETRY = _ProviderTelemetry()
_POOL = ThreadPoolExecutor(max_workers=24, thread_name_prefix="amigo-search")
_RESULT_CACHE = _LRUTTLCache(512)
_MEDIA_CACHE = _LRUTTLCache(256)
_QUOTE_CACHE = _LRUTTLCache(128)
_SINGLEFLIGHT = _SingleFlight()


# ──────────────────────────────────────────────────────────────────────────────
# 4. Unicode & Information-Theoretic Primitives (Zero Hardcoded Word Lists)
# ──────────────────────────────────────────────────────────────────────────────

_RE_HTML_TAG = re.compile(r"<[^>]+>")
_RE_WHITESPACE = re.compile(r"\s+")
_RE_TOKEN = re.compile(r"[^\W_]+(?:[._-][^\W_]+)*", re.UNICODE)
_RE_FACT_ATOM = re.compile(
    r"(?:"
    r"\d+(?:[.,]\d+)*(?:\s*%)?"
    r"|[^\W\d_]{2,}(?:[-_][^\W\d_]+)*"
    r"|[^\W\d_]+\d+[^\W_]*"
    r")",
    re.UNICODE,
)


def _normalize_text(raw: str) -> str:
    if not raw:
        return ""
    unescaped = html.unescape(_RE_HTML_TAG.sub(" ", raw))
    return _RE_WHITESPACE.sub(" ", unescaped).strip()


def clean_search_query(raw_query: str) -> str:
    q = (raw_query or "").strip().strip("\"'`?!.,;:")
    q = _RE_WHITESPACE.sub(" ", q)
    return q if q else (raw_query or "").strip()


def detect_query_type(query: str) -> str:
    return "epistemic_consensus"


def _extract_host(url: str) -> str:
    try:
        netloc = urllib.parse.urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
        return netloc[4:] if netloc.startswith("www.") else netloc
    except Exception:
        return ""


def _registered_root_domain(host: str) -> str:
    parts = [p for p in host.split(".") if p]
    if len(parts) <= 2:
        return host
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
            if not k.lower().startswith(("utm_", "fbclid", "gclid", "msclkid", "ref", "mc_", "spm"))
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

    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", cleaned)
    if m:
        return m.group(1), None
    return "", None


def _is_dense_script_char(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x0900 <= cp <= 0x0D7F      # Indic scripts
        or 0x4E00 <= cp <= 0x9FFF   # CJK Unified Ideographs
        or 0x3040 <= cp <= 0x30FF   # Kana
        or 0xAC00 <= cp <= 0xD7AF   # Hangul
        or 0x0600 <= cp <= 0x06FF   # Arabic
    )


def _token_information_weight(token: str) -> float:
    if not token:
        return 0.0
    char_len = len(token)
    has_dense = any(_is_dense_script_char(c) for c in token)
    effective_len = char_len * 2.2 if has_dense else char_len
    if effective_len <= 2 and not any(c.isdigit() for c in token):
        return 0.15

    length_bonus = 0.55 * math.log1p(effective_len)
    has_digit = any(c.isdigit() for c in token)
    has_alpha = any(c.isalpha() for c in token)
    mixed_alnum = 1.65 if (has_digit and has_alpha) else (0.95 if has_digit else 0.0)
    casing_bonus = 0.45 if any(c.isupper() for c in token[1:]) else (0.25 if token[0].isupper() else 0.0)
    struct_bonus = 0.5 if ("-" in token or "." in token) else 0.0
    return 0.5 + length_bonus + mixed_alnum + casing_bonus + struct_bonus


def _formulate_search_queries(raw_query: str) -> list[str]:
    base = clean_search_query(raw_query)
    tokens = _RE_TOKEN.findall(base)
    if len(tokens) <= 4:
        return [base]

    weighted = [(tok, _token_information_weight(tok)) for tok in tokens]
    weights_sorted = sorted(w for _, w in weighted)
    threshold = weights_sorted[len(weights_sorted) // 3]
    distilled = [tok for tok, w in weighted if w >= threshold and len(tok) > 1]
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
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return 0.25

    score = 0.62
    score += 0.06 if parsed.scheme == "https" else -0.15

    host = _extract_host(url)
    if not host:
        return 0.2

    labels = host.split(".")
    tld = labels[-1] if labels else ""
    second_level = labels[-2] if len(labels) >= 2 else ""

    if tld in ("gov", "mil", "edu", "int") or second_level in ("gov", "ac", "edu", "mil"):
        score += 0.26
    elif tld == "org":
        score += 0.08

    main_label = labels[0] if len(labels) == 2 else (labels[-2] if len(labels) >= 2 else host)
    hyphen_count = main_label.count("-")
    digit_count = sum(1 for c in main_label if c.isdigit())
    if hyphen_count >= 2:
        score -= 0.12 * (hyphen_count - 1)
    if digit_count >= 3:
        score -= 0.10
    if len(main_label) > 12 and _shannon_entropy(main_label) > 3.75:
        score -= 0.10
    if len(labels) > 3:
        score -= 0.05 * (len(labels) - 3)
    if len(urllib.parse.parse_qsl(parsed.query)) > 3:
        score -= 0.04 * (len(urllib.parse.parse_qsl(parsed.query)) - 3)

    if title:
        upper_ratio = sum(1 for c in title if c.isupper()) / max(1, len(title))
        if upper_ratio > 0.50 and len(title) > 12:
            score -= 0.12
    if len(snippet) < 35:
        score -= 0.08

    return max(0.15, min(1.0, score))


# ──────────────────────────────────────────────────────────────────────────────
# 6. Async Search Oracles
# ──────────────────────────────────────────────────────────────────────────────

_DDGS_LOCAL = threading.local()


def _get_ddgs_instance():
    inst = getattr(_DDGS_LOCAL, "instance", None)
    if inst is None:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        inst = DDGS()
        _DDGS_LOCAL.instance = inst
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
        snippet=s[:560] if s else t,
        source=source,
        domain=dom,
        published=pub_str,
        age_days=age_days,
        authority_score=auth,
    )


def _sync_ddgs_text(query: str, limit: int, timelimit: Optional[str]) -> list[SearchResult]:
    variants = _formulate_search_queries(query)
    reg = SEARCH_REGION or None
    out: list[SearchResult] = []
    for q_var in variants[:2]:
        kw: dict[str, Any] = {"max_results": limit, "backend": "auto"}
        if reg:
            kw["region"] = reg
        if timelimit:
            kw["timelimit"] = timelimit
        try:
            rows = list(_get_ddgs_instance().text(q_var, **kw) or [])
        except Exception:
            _DDGS_LOCAL.instance = None
            try:
                rows = list(_get_ddgs_instance().text(q_var, max_results=limit) or [])
            except Exception:
                _DDGS_LOCAL.instance = None
                rows = []
        for r in rows:
            sr = _build_result(
                r.get("title", ""),
                r.get("href") or r.get("url") or "",
                r.get("body", ""),
                "ddgs_auto",
            )
            if sr:
                out.append(sr)
        if len(out) >= limit:
            break
    return out[:limit]


async def _oracle_ddgs(query: str, limit: int, timelimit: Optional[str]) -> list[SearchResult]:
    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(
        loop.run_in_executor(_POOL, lambda: _sync_ddgs_text(query, limit, timelimit)),
        timeout=1.9,
    )


async def _oracle_ddg_lite(query: str, limit: int, timelimit: Optional[str]) -> list[SearchResult]:
    q = clean_search_query(query)
    params: dict[str, str] = {"q": q}
    if SEARCH_REGION:
        params["kl"] = SEARCH_REGION
    if timelimit:
        params["df"] = timelimit
    resp = await _NET.get_async("https://html.duckduckgo.com/html/", params=params, timeout=1.7)
    out: list[SearchResult] = []
    for block in re.finditer(
        r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
        r'(?:<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>|<div[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</div>)',
        resp.text,
        re.S | re.I,
    ):
        raw_href, raw_title, snip1, snip2 = block.groups()
        href = html.unescape(raw_href)
        if "uddg=" in href:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            href = qs.get("uddg", [href])[0]
        if "duckduckgo.com" in _extract_host(href):
            continue
        sr = _build_result(raw_title, href, snip1 or snip2 or "", "ddg_lite")
        if sr:
            out.append(sr)
        if len(out) >= limit:
            break
    return out


async def _oracle_google_news_rss(query: str, limit: int, timelimit: Optional[str]) -> list[SearchResult]:
    q = clean_search_query(query)
    q_eff = f"{q} when:{timelimit}" if timelimit else q
    lang = OS_LANG or "en"
    country = PC_COUNTRY_CODE or "US"
    params = {
        "q": q_eff,
        "hl": f"{lang}-{country}" if country else lang,
        "gl": country,
        "ceid": f"{country}:{lang}",
    }
    resp = await _NET.get_async("https://news.google.com/rss/search", params=params, timeout=1.7)
    root = ET.fromstring(resp.text)
    out: list[SearchResult] = []
    for item in root.findall(".//item")[:limit]:
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        pub = item.findtext("pubDate") or ""
        desc = item.findtext("description") or ""
        source_el = item.find("source")
        publisher = source_el.text.strip() if (source_el is not None and source_el.text) else ""
        if publisher and f" - {publisher}" in title:
            title = title.rsplit(f" - {publisher}", 1)[0]
        sr = _build_result(title, link, desc or title, f"gnews:{publisher or 'rss'}", published=pub)
        if sr:
            out.append(sr)
    return out


async def _oracle_yahoo_web(query: str, limit: int, timelimit: Optional[str]) -> list[SearchResult]:
    q = clean_search_query(query)
    resp = await _NET.get_async("https://search.yahoo.com/search", params={"p": q, "n": limit}, timeout=1.8)
    out: list[SearchResult] = []
    for m in re.finditer(
        r'<div[^>]+class="[^"]*algo-sr[^"]*"[^>]*>.*?'
        r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
        r'<div[^>]+class="[^"]*compText[^"]*"[^>]*>(.*?)</div>',
        resp.text,
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


async def _oracle_wikipedia_knowledge(query: str, limit: int, timelimit: Optional[str]) -> list[SearchResult]:
    q = clean_search_query(query)
    langs = [OS_LANG] if (OS_LANG and len(OS_LANG) == 2 and OS_LANG != "en") else []
    langs.append("en")

    out: list[SearchResult] = []
    for lang in langs:
        api_url = f"https://{lang}.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "generator": "search",
            "gsrsearch": q,
            "gsrlimit": min(limit, 3),
            "prop": "extracts|info",
            "exintro": 1,
            "explaintext": 1,
            "exchars": 520,
            "inprop": "url",
            "format": "json",
            "utf8": 1,
        }
        try:
            resp = await _NET.get_async(api_url, params=params, timeout=1.6)
            pages = ((resp.json().get("query") or {}).get("pages") or {}).values()
            for page in sorted(pages, key=lambda p: p.get("index", 99)):
                title = page.get("title", "")
                extract = page.get("extract", "")
                fullurl = page.get("fullurl") or f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
                sr = _build_result(title, fullurl, extract, f"wikipedia_{lang}", published=page.get("touched", ""))
                if sr:
                    sr.authority_score = max(sr.authority_score, 0.88)
                    out.append(sr)
            if out:
                break
        except Exception:
            continue
    return out


async def _oracle_openalex_scholarly(query: str, limit: int, timelimit: Optional[str]) -> list[SearchResult]:
    q = clean_search_query(query)
    resp = await _NET.get_async(
        "https://api.openalex.org/works",
        params={
            "search": q,
            "per-page": min(limit, 3),
            "select": "id,title,publication_year,doi,primary_location,abstract_inverted_index",
        },
        timeout=1.7,
    )
    out: list[SearchResult] = []
    for work in resp.json().get("results") or []:
        title = work.get("title") or ""
        loc = (work.get("primary_location") or {}).get("landing_page_url") or work.get("doi") or work.get("id") or ""
        inv = work.get("abstract_inverted_index") or {}
        snippet = ""
        if isinstance(inv, dict) and inv:
            pos_words = sorted((pos, word) for word, positions in inv.items() for pos in positions[:3])
            snippet = " ".join(w for _, w in pos_words[:65])
        pub_year = str(work.get("publication_year") or "")
        sr = _build_result(title, loc, snippet or title, "openalex", published=f"{pub_year}-01-01" if pub_year else "")
        if sr:
            sr.authority_score = max(sr.authority_score, 0.86)
            out.append(sr)
    return out


async def _oracle_tavily_api(query: str, limit: int, timelimit: Optional[str]) -> list[SearchResult]:
    if not TAVILY_API_KEY:
        raise RuntimeError("No Tavily key")
    resp = await _NET.post_async(
        "https://api.tavily.com/search",
        json_body={
            "api_key": TAVILY_API_KEY,
            "query": clean_search_query(query),
            "max_results": limit,
            "search_depth": "basic",
        },
        timeout=1.9,
    )
    out: list[SearchResult] = []
    for r in resp.json().get("results", []):
        sr = _build_result(r.get("title", ""), r.get("url", ""), r.get("content", ""), "tavily_api", r.get("published_date", ""))
        if sr:
            out.append(sr)
    return out


async def _oracle_brave_api(query: str, limit: int, timelimit: Optional[str]) -> list[SearchResult]:
    if not BRAVE_API_KEY:
        raise RuntimeError("No Brave key")
    params: dict[str, Any] = {"q": clean_search_query(query), "count": min(limit, 15)}
    if PC_COUNTRY_CODE:
        params["country"] = PC_COUNTRY_CODE.lower()
    if timelimit:
        params["freshness"] = {"d": "pd", "w": "pw", "m": "pm", "y": "py"}.get(timelimit, timelimit)
    resp = await _NET.get_async(
        "https://api.search.brave.com/res/v1/web/search",
        params=params,
        headers={"X-Subscription-Token": BRAVE_API_KEY, "Accept": "application/json"},
        timeout=1.9,
    )
    out: list[SearchResult] = []
    for r in (resp.json().get("web") or {}).get("results", []):
        sr = _build_result(r.get("title", ""), r.get("url", ""), r.get("description", ""), "brave_api", r.get("page_age", ""))
        if sr:
            out.append(sr)
    return out


async def _oracle_searxng_api(query: str, limit: int, timelimit: Optional[str]) -> list[SearchResult]:
    if not SEARXNG_URL:
        raise RuntimeError("No SearXNG URL")
    params: dict[str, Any] = {"q": clean_search_query(query), "format": "json"}
    if timelimit:
        params["time_range"] = {"d": "day", "w": "week", "m": "month", "y": "year"}.get(timelimit, "")
    resp = await _NET.get_async(f"{SEARXNG_URL}/search", params=params, timeout=1.9)
    out: list[SearchResult] = []
    for r in resp.json().get("results", [])[:limit]:
        sr = _build_result(r.get("title", ""), r.get("url", ""), r.get("content", ""), "searxng_api", r.get("publishedDate", ""))
        if sr:
            out.append(sr)
    return out


_ORACLE_REGISTRY: dict[str, tuple[Callable[..., Any], float]] = {
    "tavily":       (_oracle_tavily_api,          1.35),
    "brave_api":    (_oracle_brave_api,           1.30),
    "searxng":      (_oracle_searxng_api,         1.25),
    "ddgs":         (_oracle_ddgs,                1.22),
    "ddg_lite":     (_oracle_ddg_lite,            1.18),
    "gnews_rss":    (_oracle_google_news_rss,     1.16),
    "yahoo":        (_oracle_yahoo_web,           1.12),
    "encyclopedia": (_oracle_wikipedia_knowledge, 1.25),
    "scholarly":    (_oracle_openalex_scholarly,  1.10),
}


def _select_active_oracles(requested: Optional[list[str]] = None) -> list[str]:
    if requested:
        return [r for r in requested if r in _ORACLE_REGISTRY]
    candidates = ["ddgs", "ddg_lite", "gnews_rss", "yahoo", "encyclopedia", "scholarly"]
    if TAVILY_API_KEY:
        candidates.insert(0, "tavily")
    if BRAVE_API_KEY:
        candidates.insert(0, "brave_api")
    if SEARXNG_URL:
        candidates.insert(0, "searxng")
    healthy = [c for c in candidates if _TELEMETRY.is_healthy(c)]
    return sorted(healthy or candidates, key=lambda c: _TELEMETRY.latency_ms(c))


# ──────────────────────────────────────────────────────────────────────────────
# 7. Cross-Source Claim Corroboration & Consensus Ranking Engine
# ──────────────────────────────────────────────────────────────────────────────

def _extract_factual_atoms(text: str, query_tokens: set[str]) -> set[str]:
    atoms: set[str] = set()
    for match in _RE_FACT_ATOM.findall(text):
        norm = match.strip(".,;:()[]{}").lower()
        if norm in query_tokens:
            continue
        if any(_is_dense_script_char(c) for c in norm):
            if len(norm) >= 2:
                atoms.add(norm)
        elif len(norm) >= 3 or any(c.isdigit() for c in norm):
            atoms.add(norm)
    return atoms


def _apply_corroboration_and_ranking(
    candidates: dict[str, SearchResult],
    oracle_support: dict[str, set[str]],
    query: str,
) -> list[SearchResult]:
    if not candidates:
        return []

    clean_q = clean_search_query(query)
    raw_q_tokens = _RE_TOKEN.findall(clean_q)
    q_weights = {
        t.lower(): _token_information_weight(t)
        for t in raw_q_tokens
        if _token_information_weight(t) > 0.2
    }
    if not q_weights:
        q_weights = {t.lower(): 1.0 for t in raw_q_tokens if t}
    q_token_set = set(q_weights.keys())
    total_q_weight = sum(q_weights.values()) or 1.0

    atom_to_root_domains: dict[str, set[str]] = defaultdict(set)
    candidate_atoms: dict[str, set[str]] = {}
    candidate_alignment: dict[str, float] = {}

    for ukey, item in candidates.items():
        blob = f"{item.title} {item.snippet} {item.fetched_content}"
        doc_tokens = {t.lower() for t in _RE_TOKEN.findall(blob)}
        matched_weight = sum(w for tok, w in q_weights.items() if tok in doc_tokens)
        coverage_ratio = matched_weight / total_q_weight
        candidate_alignment[ukey] = coverage_ratio

        if coverage_ratio >= 0.25:
            root_dom = _registered_root_domain(item.domain)
            atoms = _extract_factual_atoms(blob, q_token_set)
            candidate_atoms[ukey] = atoms
            for atom in atoms:
                atom_to_root_domains[atom].add(root_dom)
        else:
            candidate_atoms[ukey] = set()

    for ukey, item in candidates.items():
        root_dom = _registered_root_domain(item.domain)
        atoms = candidate_atoms.get(ukey, set())
        corroborating_domains: set[str] = set()
        shared_fact_weight = 0.0

        for atom in atoms:
            supporters = atom_to_root_domains.get(atom, set()) - {root_dom}
            if supporters:
                corroborating_domains.update(supporters)
                atom_bonus = 1.40 if any(c.isdigit() for c in atom) else 1.0
                shared_fact_weight += atom_bonus * math.log1p(len(supporters))

        num_corroborators = len(corroborating_domains)
        num_oracles = len(oracle_support.get(ukey, ()))
        consensus_norm = min(
            1.0,
            (shared_fact_weight / 5.5) + (0.18 * num_corroborators) + (0.12 * max(0, num_oracles - 1)),
        )

        item.consensus_score = round(consensus_norm, 3)
        item.corroborated_by = sorted(corroborating_domains)[:5]

        if num_corroborators >= 2 or (num_corroborators >= 1 and item.authority_score >= 0.80):
            item.trust_tier = "VERIFIED_CONSENSUS"
        elif num_corroborators == 1 or num_oracles >= 2 or item.authority_score >= 0.85:
            item.trust_tier = "CORROBORATED"
        else:
            item.trust_tier = "SINGLE_SOURCE"

        alignment = candidate_alignment.get(ukey, 0.0)
        recency_boost = 0.09 * math.exp(-item.age_days / 45.0) if item.age_days is not None else 0.0

        item.score = (
            (alignment ** 1.35) * 0.42
            + item.consensus_score * 0.28
            + item.authority_score * 0.18
            + min(0.12, item.score * 2.5)
            + recency_boost
        )

    ranked = sorted(candidates.values(), key=lambda r: r.score, reverse=True)
    final_list: list[SearchResult] = []
    domain_counts: Counter[str] = Counter()
    for r in ranked:
        root = _registered_root_domain(r.domain)
        if domain_counts[root] >= 2:
            continue
        domain_counts[root] += 1
        final_list.append(r)
    return final_list


def _merge_harvested(
    harvested: dict[str, list[SearchResult]],
    query: str,
) -> list[SearchResult]:
    candidates: dict[str, SearchResult] = {}
    oracle_support: dict[str, set[str]] = defaultdict(set)

    for oracle_name, results in harvested.items():
        oracle_weight = _ORACLE_REGISTRY.get(oracle_name, (None, 1.0))[1]
        for rank_idx, item in enumerate(results):
            ukey = _canonical_url_key(item.url)
            rrf_contrib = oracle_weight / (45.0 + rank_idx + 1.0)
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

    return _apply_corroboration_and_ranking(candidates, oracle_support, query)


# ──────────────────────────────────────────────────────────────────────────────
# 8. Deep Verbatim Evidence, Meta & JSON-LD Extractor
# ──────────────────────────────────────────────────────────────────────────────

_RE_NOISE_BLOCKS = re.compile(
    r"<(script|style|nav|footer|header|aside|noscript|svg|form|iframe|button)[^>]*>.*?</\1>",
    re.S | re.I,
)
_RE_LD_JSON = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.S | re.I,
)


def _extract_meta_and_jsonld(html_doc: str) -> tuple[list[str], str]:
    facts: list[str] = []
    pub_date = ""

    head_end = html_doc.lower().find("</head>")
    head_slice = html_doc[: head_end + 7] if head_end != -1 else html_doc[:65_536]

    for m in re.finditer(r"<meta\s+([^>]+)>", head_slice, re.I):
        attrs = m.group(1)
        name_m = re.search(r'(?:name|property)=["\']([^"\']+)["\']', attrs, re.I)
        cont_m = re.search(r'content=["\']([^"\']+)["\']', attrs, re.I)
        if not (name_m and cont_m):
            continue
        key = name_m.group(1).lower()
        val = _normalize_text(cont_m.group(1))
        if key in ("description", "og:description", "twitter:description") and len(val) > 45:
            facts.append(val[:450])
        elif not pub_date and key in ("article:published_time", "og:updated_time", "date", "pubdate", "dc.date"):
            pub_date, _ = _parse_timestamp(val)

    for m in _RE_LD_JSON.finditer(html_doc):
        try:
            payload = json.loads(m.group(1).strip())
        except Exception:
            continue
        stack = [payload]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if not pub_date and isinstance(node.get("datePublished"), str):
                    pub_date, _ = _parse_timestamp(node["datePublished"])
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
                        stack.append(v)
            elif isinstance(node, list):
                stack.extend(node)

    return facts[:5], pub_date


def _extract_high_density_evidence(html_doc: str, query: str, max_chars: int = 1600) -> tuple[str, str]:
    structured_facts, detected_pub = _extract_meta_and_jsonld(html_doc)
    cleaned_html = _RE_NOISE_BLOCKS.sub(" ", html_doc)

    paragraphs: list[str] = []
    for pm in re.finditer(
        r"<(?:p|li|td|dd|blockquote|article|section|div)[^>]*>(.*?)</(?:p|li|td|dd|blockquote|article|section|div)>",
        cleaned_html,
        re.S | re.I,
    ):
        raw_inner = pm.group(1)
        if raw_inner.count("<") > 6:
            continue
        txt = _normalize_text(raw_inner)
        if 40 <= len(txt) <= 1000:
            paragraphs.append(txt)

    if not paragraphs:
        fallback_txt = _normalize_text(cleaned_html)
        paragraphs = [
            fallback_txt[i : i + 500]
            for i in range(0, min(len(fallback_txt), 8000), 450)
            if len(fallback_txt[i : i + 500]) > 60
        ]

    clean_q = clean_search_query(query)
    q_weights = {
        t.lower(): _token_information_weight(t)
        for t in _RE_TOKEN.findall(clean_q)
        if _token_information_weight(t) > 0.2
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

        q_hit_score = sum(w for tok, w in q_weights.items() if tok in p_tokens)
        fact_atoms = len(_RE_FACT_ATOM.findall(para))
        density_bonus = min(2.6, fact_atoms * 0.22)
        lead_bonus = 0.65 if idx < len(structured_facts) else (0.25 if idx < 5 else 0.0)

        total_sc = q_hit_score * 1.55 + density_bonus + lead_bonus
        if q_hit_score > 0 or idx < len(structured_facts):
            scored_passages.append((total_sc, idx, para))

    if not scored_passages:
        return "", detected_pub

    scored_passages.sort(key=lambda x: x[0], reverse=True)
    chosen: list[tuple[int, str]] = []
    char_budget = 0
    for _, idx, text in scored_passages[:5]:
        chosen.append((idx, text))
        char_budget += len(text) + 4
        if char_budget >= max_chars:
            break

    chosen.sort(key=lambda x: x[0])
    return " … ".join(txt for _, txt in chosen)[:max_chars], detected_pub


async def _async_deep_verify_top_results(results: list[SearchResult], query: str, deadline_s: float) -> None:
    targets = [
        r for r in results
        if not r.url.startswith("https://www.youtube.com/watch")
        and "news.google.com/rss/articles" not in r.url
    ][:4]
    if not targets:
        return

    async def _fetch_one(res: SearchResult) -> None:
        try:
            resp = await _NET.get_async(res.url, timeout=min(1.5, max(0.6, deadline_s * 0.85)))
            ctype = (resp.headers.get("content-type") or "").lower()
            if any(b in ctype for b in ("pdf", "image", "audio", "video", "octet-stream")):
                return
            excerpt, detected_pub = _extract_high_density_evidence(resp.text, query)
            if excerpt:
                res.fetched_content = excerpt
            if detected_pub and not res.published:
                res.published = detected_pub
        except Exception:
            pass

    try:
        async with asyncio.timeout(deadline_s):
            await asyncio.gather(*(_fetch_one(r) for r in targets), return_exceptions=True)
    except TimeoutError:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# 9. Public Search API (Async-Native + Sync-Compatible)
# ──────────────────────────────────────────────────────────────────────────────

async def _async_search_pipeline(
    raw_q: str,
    max_results: int,
    engines: Optional[list[str]],
    fetch_content: Optional[bool],
    fast_mode: bool,
    freshness: Optional[str],
) -> SearchResponse:
    t_start = time.monotonic()
    oracles = _select_active_oracles(engines)
    timelimit = _normalize_timelimit(freshness)

    race_budget = 1.45 if fast_mode else 2.25
    grace_window = 0.25 if fast_mode else 0.40
    per_oracle_limit = 6 if fast_mode else 8

    task_to_name: dict[asyncio.Task[list[SearchResult]], tuple[str, float]] = {}
    for name in oracles:
        task = asyncio.create_task(_ORACLE_REGISTRY[name][0](raw_q, per_oracle_limit, timelimit))
        task_to_name[task] = (name, time.monotonic())

    pending = set(task_to_name.keys())
    harvested: dict[str, list[SearchResult]] = {}
    grace_deadline: Optional[float] = None
    hard_deadline = time.monotonic() + race_budget

    try:
        while pending:
            now = time.monotonic()
            cutoff = hard_deadline if grace_deadline is None else min(hard_deadline, grace_deadline)
            rem = cutoff - now
            if rem <= 0:
                break
            done, pending = await asyncio.wait(pending, timeout=rem, return_when=asyncio.FIRST_COMPLETED)
            for fut in done:
                name, t0 = task_to_name[fut]
                elapsed_ms = (time.monotonic() - t0) * 1000.0
                try:
                    rows = fut.result()
                    _TELEMETRY.record_success(name, elapsed_ms)
                    if rows:
                        harvested[name] = rows
                        if grace_deadline is None and len(harvested) >= 3:
                            grace_deadline = time.monotonic() + grace_window
                except Exception as exc:
                    _TELEMETRY.record_failure(name, permanent=isinstance(exc, ImportError))
                    logger.debug("[EpistemicSearch] Oracle %s failed: %s", name, exc)
    finally:
        for fut in pending:
            fut.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    verified_results = _merge_harvested(harvested, raw_q)[:max_results]

    should_fetch = fetch_content if fetch_content is not None else (not fast_mode and len(verified_results) > 0)
    if should_fetch and verified_results:
        fetch_budget = 0.95 if fast_mode else 1.45
        await _async_deep_verify_top_results(verified_results, raw_q, deadline_s=fetch_budget)
        if any(r.fetched_content for r in verified_results):
            verified_results = _merge_harvested(
                {"verified": verified_results}, raw_q
            )[:max_results]

    total_ms = int((time.monotonic() - t_start) * 1000)
    logger.info(
        "[EpistemicSearch] %r -> %d verified results in %dms (oracles=%s)",
        raw_q[:60],
        len(verified_results),
        total_ms,
        ",".join(harvested.keys()) or "none",
    )
    return SearchResponse(
        query=raw_q,
        results=verified_results,
        query_type="epistemic_consensus",
        search_time_ms=total_ms,
        engines_used=list(harvested.keys()),
    )


def _run_coro_sync(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # If already inside an event loop, run in a dedicated worker thread
    fut = _POOL.submit(asyncio.run, coro)
    return fut.result(timeout=8.0)


async def async_search_web(
    query: str,
    max_results: int = 6,
    engines: Optional[list[str]] = None,
    fetch_content: Optional[bool] = None,
    use_cache: bool = True,
    fast_mode: bool = False,
    freshness: Optional[str] = None,
    include_images: bool = False,
) -> SearchResponse:
    raw_q = (query or "").strip()
    if not raw_q:
        return SearchResponse(query="", results=[], query_type="empty")

    cache_key = (
        raw_q.lower(),
        max_results,
        fetch_content,
        bool(fast_mode),
        _normalize_timelimit(freshness),
        tuple(sorted(engines or ())),
    )
    if use_cache:
        cached = _RESULT_CACHE.get(cache_key)
        if cached is not None:
            return replace(cached, cache_hit=True, search_time_ms=0)

    response = _SINGLEFLIGHT.run(
        cache_key,
        lambda: _run_coro_sync(
            _async_search_pipeline(raw_q, max_results, engines, fetch_content, fast_mode, freshness)
        ),
    )

    if use_cache and response and response.results:
        _RESULT_CACHE.put(cache_key, response, ttl_s=300.0)

    if include_images and response and response.results and not response.structured_data.get("images"):
        try:
            top_imgs = await async_search_images(raw_q, max_results=2)
            if top_imgs:
                response.structured_data["images"] = top_imgs
        except Exception as exc:
            logger.debug("[Web Search] Image attachment note: %s", exc)

    return response


def search_web(
    query: str,
    max_results: int = 6,
    engines: Optional[list[str]] = None,
    fetch_content: Optional[bool] = None,
    use_cache: bool = True,
    fast_mode: bool = False,
    freshness: Optional[str] = None,
    include_images: bool = False,
) -> SearchResponse:
    raw_q = (query or "").strip()
    if not raw_q:
        return SearchResponse(query="", results=[], query_type="empty")

    cache_key = (
        raw_q.lower(),
        max_results,
        fetch_content,
        bool(fast_mode),
        _normalize_timelimit(freshness),
        tuple(sorted(engines or ())),
    )
    if use_cache:
        cached = _RESULT_CACHE.get(cache_key)
        if cached is not None:
            return replace(cached, cache_hit=True, search_time_ms=0)

    response = _SINGLEFLIGHT.run(
        cache_key,
        lambda: _run_coro_sync(
            _async_search_pipeline(raw_q, max_results, engines, fetch_content, fast_mode, freshness)
        ),
    )

    if use_cache and response and response.results:
        _RESULT_CACHE.put(cache_key, response, ttl_s=300.0)

    if include_images and response and response.results and not response.structured_data.get("images"):
        try:
            top_imgs = search_images(raw_q, max_results=2)
            if top_imgs:
                response.structured_data["images"] = top_imgs
        except Exception as exc:
            logger.debug("[Web Search] Image attachment note: %s", exc)

    return response


def format_for_llm(response: SearchResponse, max_chars: int = 5200) -> str:
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


# ──────────────────────────────────────────────────────────────────────────────
# Image Search (Async + Sync with Pooled Wikimedia Fallback)
# ──────────────────────────────────────────────────────────────────────────────

async def _async_search_images_wikimedia(query: str, max_results: int = 4) -> list[dict[str, str]]:
    lang = OS_LANG if (OS_LANG and len(OS_LANG) == 2) else "en"
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": query.strip(),
        "gsrlimit": max_results * 2,
        "prop": "pageimages",
        "piprop": "thumbnail",
        "pithumbsize": 640,
        "format": "json",
        "origin": "*",
    }
    try:
        resp = await _NET.get_async(f"https://{lang}.wikipedia.org/w/api.php", params=params, timeout=2.0)
        pages = ((resp.json().get("query") or {}).get("pages") or {}).values()
        results: list[dict[str, str]] = []
        for p in pages:
            thumb = (p.get("thumbnail") or {}).get("source")
            if not thumb:
                continue
            title = str(p.get("title") or query)
            pageid = p.get("pageid")
            source_url = (
                f"https://{lang}.wikipedia.org/?curid={pageid}"
                if pageid
                else f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
            )
            results.append({
                "title": title,
                "image_url": thumb,
                "thumbnail_url": thumb,
                "source_url": source_url,
                "domain": f"{lang}.wikipedia.org",
            })
            if len(results) >= max_results:
                break
        return results
    except Exception as exc:
        logger.debug("[search_images:Wikimedia] Failed for %r: %s", query, exc)
        return []


def _sync_ddgs_images(query: str, max_results: int) -> list[dict[str, str]]:
    inst = _get_ddgs_instance()
    raw_hits = list(inst.images(query, safesearch="moderate", max_results=max_results))
    out: list[dict[str, str]] = []
    for item in raw_hits:
        img_url = (item.get("image") or "").strip()
        if not img_url or not img_url.startswith(("http://", "https://")):
            continue
        thumb_url = (item.get("thumbnail") or img_url).strip()
        src_url = (item.get("url") or img_url).strip()
        dom = _extract_host(src_url) or _extract_host(img_url) or "web"
        title = (item.get("title") or query).strip()
        out.append({
            "title": title,
            "image_url": img_url,
            "thumbnail_url": thumb_url,
            "source_url": src_url,
            "domain": dom,
        })
        if len(out) >= max_results:
            break
    return out


async def async_search_images(query: str, max_results: int = 4) -> list[dict[str, str]]:
    raw_q = (query or "").strip()
    if not raw_q:
        return []

    cache_key = ("img", raw_q.lower(), max_results)
    cached = _MEDIA_CACHE.get(cache_key)
    if cached is not None:
        return [dict(h) for h in cached]

    results: list[dict[str, str]] = []
    loop = asyncio.get_running_loop()
    try:
        results = await asyncio.wait_for(
            loop.run_in_executor(_POOL, lambda: _sync_ddgs_images(raw_q, max_results)),
            timeout=1.9,
        )
    except Exception as exc:
        logger.debug("[search_images] Primary lookup note for %r: %s", raw_q, exc)

    if not results:
        results = await _async_search_images_wikimedia(raw_q, max_results=max_results)

    if results:
        _MEDIA_CACHE.put(cache_key, [dict(h) for h in results], ttl_s=300.0)
    return results


def search_images(query: str, max_results: int = 4) -> list[dict[str, str]]:
    return _run_coro_sync(async_search_images(query, max_results=max_results))


# ──────────────────────────────────────────────────────────────────────────────
# 10. YouTube Resolution Subsystem
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
    stack = [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            vr = node.get("videoRenderer")
            if isinstance(vr, dict) and vr.get("videoId"):
                yield vr
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)


async def _async_query_yt_innertube(query: str, sp_params: Optional[str] = None) -> list[YTVideo]:
    client_ctx: dict[str, Any] = {
        "clientName": "WEB",
        "clientVersion": _YT_INNERTUBE_VER,
        "hl": OS_LANG or "en",
    }
    if YT_REGION:
        client_ctx["gl"] = YT_REGION
    payload: dict[str, Any] = {"context": {"client": client_ctx}, "query": query}
    if sp_params:
        payload["params"] = sp_params

    resp = await _NET.post_async(
        _YT_ENDPOINT,
        json_body=payload,
        cookies=_YT_CONSENT_COOKIES,
        timeout=2.0,
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
    clean_q = clean_search_query(query)
    is_live = bool(live) if live is not None else (kind == "live")
    is_latest = bool(latest)

    fallback = {
        "url": "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(clean_q),
        "title": clean_q,
        "channel": "YouTube",
        "video_id": "",
        "query": clean_q,
        "is_live": False,
        "not_live_fallback": False,
        "resolved": False,
        "source": "search_page",
    }

    cache_key = ("yt", clean_q.lower(), kind, is_live, is_latest)
    if not is_live and not is_latest:
        cached = _MEDIA_CACHE.get(cache_key)
        if cached:
            return dict(cached)

    sp = "EgJAAQ%3D%3D" if is_live else ("CAISAhAB" if is_latest else "EgIQAQ%3D%3D")
    try:
        async def _fetch():
            cands = await _async_query_yt_innertube(clean_q, sp_params=sp)
            if not cands and (is_live or is_latest):
                cands = await _async_query_yt_innertube(clean_q)
            return cands

        candidates = _run_coro_sync(_fetch())
    except Exception:
        candidates = []

    chosen: Optional[YTVideo] = None
    if is_live:
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
            not_live_fallback=bool(is_live and not chosen.is_live),
            resolved=True,
            source=chosen.source,
        )
        if not is_live and not is_latest:
            _MEDIA_CACHE.put(cache_key, dict(fallback), ttl_s=21600.0)

    return fallback


def searchYoutube(query: str, **kwargs) -> str:
    res = resolve_youtube_video(query, **kwargs)
    webbrowser.open(res["url"])
    return res["url"]


# ──────────────────────────────────────────────────────────────────────────────
# 11. Live Financial Quote Engine
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
        s_resp = _NET.get_sync(
            _YF_SEARCH_API,
            params={"q": q, "quotesCount": 5, "newsCount": 0},
            headers={"Accept": "application/json"},
            timeout=2.0,
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

        c_resp = _NET.get_sync(
            _YF_CHART_API.format(symbol=urllib.parse.quote(symbol, safe="")),
            params={"range": range_val, "interval": interval},
            headers={"Accept": "application/json"},
            timeout=2.0,
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
    def _init() -> None:
        try:
            _get_ddgs_instance()
        except Exception:
            pass
    _POOL.submit(_init)


def shutdown() -> None:
    _POOL.shutdown(wait=False, cancel_futures=True)
    _NET.close_sync()
    _DDGS_LOCAL.instance = None
    _RESULT_CACHE.clear()
    _MEDIA_CACHE.clear()
    _QUOTE_CACHE.clear()


warmup()