"""
Searchnow.py — Web search, stock prices, and YouTube resolution for Amigo.
"""

import html
import json
import logging
import re
import urllib.parse
import urllib.request
import webbrowser
from typing import Optional

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


logger = logging.getLogger("amigo.search")

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
_RE_STOCK_CLEAN = re.compile(r"\b(stock|price|share|shares|quote|trading|today|current|what is|what's|the|of)\b", re.IGNORECASE)
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



def resolve_ticker(company_or_symbol: str) -> Optional[str]:
    """Resolves a company name or ticker to an official stock symbol via Yahoo Finance."""
    try:
        url = f"https://query1.finance.yahoo.com/v1/finance/search?q={urllib.parse.quote(company_or_symbol)}&quotesCount=1&newsCount=0"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        quotes = data.get("quotes", [])
        return quotes[0].get("symbol") if quotes else None
    except Exception:
        return None


def get_stock_price(symbol_or_name: str) -> str:
    """Fetches real-time stock price from Yahoo Finance API."""
    try:
        ticker = resolve_ticker(symbol_or_name) or symbol_or_name.upper().strip()
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1d"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        
        meta = data["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice") or meta.get("previousClose")
        currency = meta.get("currency", "USD")
        name = meta.get("longName") or meta.get("shortName") or ticker
        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        
        if price:
            change_str = f" ({'+' if price >= prev else ''}{round(price - prev, 2)})" if prev else ""
            return f"{name} ({ticker}) current stock price: {currency} {price:.2f}{change_str}."
    except Exception:
        pass
    return ""


def scrape_web_info(query: str, max_results: int = 3) -> str:
    """
    Fast, reliable, free web search & fact collector.
    1. Checks stock ticker / quote if financial keywords are present.
    2. Uses DDGS (DuckDuckGo Search) to retrieve verified web snippets (clean JSON, TLS spoofed, no rate-limits).
    3. Falls back to Wikipedia summary if needed for entity ground truth.
    """
    if not query or not query.strip():
        return ""

    core_q = clean_search_query(query)
    search_term = core_q or query.strip()

    snippets: list[str] = []

    # 1. Stock / Financial check if relevant
    if any(w in query.lower() for w in ("stock", "share price", "nasdaq", "nyse", "market price")):
        subject = _RE_STOCK_CLEAN.sub("", search_term).strip()
        if len(subject) >= 2:
            if price_fact := get_stock_price(subject):
                snippets.append(price_fact)

    # 2. DDGS Web Search (Fast, Structured, Free)
    try:
        from ddgs import DDGS
        ddgs_client = DDGS(timeout=5)
        results = list(ddgs_client.text(search_term, max_results=max_results))
        for r in results:
            title = (r.get("title") or "").strip()
            body = (r.get("body") or "").strip()
            if body and len(body) > 30:
                snippet = f"{title}: {body}" if title else body
                if snippet not in snippets:
                    snippets.append(snippet)
    except Exception as e:
        logger.debug(f"[DDGS Error]: {e}")

    # 3. Wikipedia Fallback (Instant authoritative facts for entities, organizations, people, concepts)
    if len(snippets) < 2:
        try:
            import wikipedia
            wiki_summary = wikipedia.summary(search_term, sentences=3, auto_suggest=True)
            if wiki_summary and len(wiki_summary.strip()) > 40:
                wiki_fact = f"Wikipedia ({search_term}): {wiki_summary.strip()}"
                if wiki_fact not in snippets:
                    snippets.insert(0, wiki_fact)
        except Exception:
            pass

    return " | ".join(snippets)[:3000] if snippets else ""


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

