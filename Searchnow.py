"""
Searchnow.py — Web search, stock prices, Wikipedia, and YouTube resolution for Amigo.
"""

import html
import json
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


def clean_search_query(raw_query: str) -> str:
    """Extract core search terms by stripping conversational prefixes and suffixes."""
    if not raw_query:
        return ""
    q = raw_query.strip()
    q = re.sub(
        r"^(?:(?:hey\s+|hi\s+|hello\s+)?amigo\s*)?(?:can\s+you\s+|could\s+you\s+)?(?:please\s+)?(?:tell\s+me\s+(?:about\s+)?|show\s+me\s+|find\s+|search\s+(?:google\s+for\s+|for\s+)?|look\s+up\s+|who\s+(?:is|was|are|were)\s+|what\s+(?:is|was|are|were)\s+|when\s+(?:is|was|did)\s+|where\s+(?:is|was|are)\s+|how\s+to\s+|google\s+)?",
        "",
        q,
        flags=re.IGNORECASE,
    ).strip()
    q = re.sub(r"\s+(?:please|for\s+me|right\s+now|now|today|online)$", "", q, flags=re.IGNORECASE).strip()
    return q if len(q) > 2 else raw_query.strip()


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


def _is_junk_snippet(text: str) -> bool:
    """Filter out navigation, ad boilerplate, or pure URLs from search snippets."""
    t = text.lower()
    if text.startswith("http") or (re.search(r"https?://", text) and len(text) < 120):
        return True
    bad_phrases = ["ad related to", "sponsored", "advertisement", "privacy policy", "terms of use", "tickertech"]
    return any(bad in t for bad in bad_phrases)


def scrape_web_info(query: str) -> str:
    """
    Multi-Engine Web Search:
    1. DuckDuckGo Instant Answer API (Factual answers)
    2. Yahoo Search (News & snippets)
    3. DuckDuckGo HTML (Fallback snippets)
    """
    if not query or not query.strip():
        return ""

    snippets = []
    core_q = clean_search_query(query)
    search_queries = [core_q] if core_q.lower() == query.lower().strip() else [core_q, query.strip()]

    # 1. Stock / Financial check
    if any(w in query.lower() for w in ("stock", "share", "price of", "shares", "nasdaq", "nyse", "quote")):
        subject = re.sub(r"\b(stock|price|share|shares|quote|trading|today|current|what is|what's|the|of)\b", "", core_q, flags=re.I).strip()
        if len(subject) >= 2:
            price_fact = get_stock_price(subject)
            if price_fact:
                snippets.append(price_fact)

    # 2. DuckDuckGo Instant Answer API
    try:
        api_url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(search_queries[0])}&format=json&no_html=1&skip_disambig=1"
        req = urllib.request.Request(api_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            
        abstract = data.get("AbstractText", "").strip()
        if len(abstract) > 40:
            snippets.append(abstract)
            
        answer = data.get("Answer", "").strip()
        if len(answer) > 5 and answer not in snippets:
            snippets.append(answer)
            
        for topic in data.get("RelatedTopics", [])[:3]:
            txt = topic.get("Text", "").strip()
            if len(txt) > 40 and txt not in snippets:
                snippets.append(txt)
    except Exception:
        pass

    # 3. Yahoo Search Snippets
    if len(snippets) < 4:
        for q in search_queries:
            try:
                url = f"https://search.yahoo.com/search?p={urllib.parse.quote(q)}"
                req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
                with urllib.request.urlopen(req, timeout=4) as resp:
                    html_content = resp.read().decode("utf-8", errors="ignore")
                
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html_content, "html.parser")
                for div in soup.select(".compText, p.lh-16, .fc-falcon, span.fc-falcon"):
                    txt = re.sub(r"\s+", " ", div.get_text(separator=" ", strip=True)).strip()
                    if len(txt) > 50 and not _is_junk_snippet(txt) and txt not in snippets:
                        snippets.append(txt)
                if len(snippets) >= 6:
                    break
            except Exception:
                pass

    # 4. DuckDuckGo HTML (fallback)
    if len(snippets) < 3:
        for q in search_queries:
            try:
                url = "https://html.duckduckgo.com/html/"
                data = urllib.parse.urlencode({"q": q, "b": ""}).encode("utf-8")
                req_headers = {**DEFAULT_HEADERS, "Referer": "https://html.duckduckgo.com/", "Content-Type": "application/x-www-form-urlencoded"}
                req = urllib.request.Request(url, data=data, headers=req_headers)
                with urllib.request.urlopen(req, timeout=4) as resp:
                    raw_html = resp.read().decode("utf-8", errors="ignore")

                found = re.findall(r'class="result__snippet[^>]*>(.*?)</a>', raw_html, re.DOTALL)
                for s in found[:4]:
                    clean = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()
                    if len(clean) > 50 and not _is_junk_snippet(clean) and clean not in snippets:
                        snippets.append(clean)
                if len(snippets) >= 5:
                    break
            except Exception:
                pass

    return " | ".join(snippets)[:3500] if snippets else ""


def searchGoogle(query: str) -> None:
    """Open Google search in the default web browser."""
    if not query:
        return
    clean_q = re.sub(r"^(show me|open|search|find|look up|what is|google)\s+", "", query, flags=re.I).strip()
    clean_q = clean_q.replace("google", "").strip() or query
    webbrowser.open(f"https://www.google.com/search?q={urllib.parse.quote(clean_q)}")


def resolve_youtube_video(query: str) -> dict:
    """Extract query, fetch top YouTube video, title, and channel."""
    q = (query or "").strip()
    # 1. Clean conversational prefixes and suffixes
    q = re.sub(r"^(?:(?:hey\s+|hi\s+|hello\s+)?amigo\s*)?(?:please\s+|can you\s+|could you\s+)?(?:open youtube|play|listen to|put on|stream|watch|search youtube for)?\s*", "", q, flags=re.I)
    q = re.sub(r"\s+(?:on youtube|in youtube|youtube|for me|please|now|right now)$", "", q, flags=re.I).strip()

    # 2. Check if user wants latest/recent uploads -> clean noise and sort by upload date
    is_latest = bool(re.search(r"\b(latest|newest|recent|new|today|yesterday)\b", q, flags=re.I))
    clean_q = re.sub(r"['’]?s?\s*\b(latest|newest|recent|new|today|yesterday|videos?|uploads?|vids?)\b", "", q, flags=re.I).strip() if is_latest else q
    clean_q = re.sub(r"\b(that\s+(?:does|do|makes?|creates?)|who\s+(?:does|do|makes?|creates?))\b", "", clean_q, flags=re.I).strip()
    clean_q = re.sub(r"\s+", " ", clean_q).strip() or q

    # 3. Choose YouTube filter: Upload Date (CAISAhAB) or Relevance (EgIQAQ%253D%253D)
    sp = "CAISAhAB" if is_latest else "EgIQAQ%253D%253D"
    search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(clean_q)}&sp={sp}"

    result = {"url": search_url, "title": clean_q.title(), "channel": "YouTube", "video_id": "", "query": clean_q}

    try:
        req = urllib.request.Request(search_url, headers=DEFAULT_HEADERS)
        with urllib.request.urlopen(req, timeout=4.5) as resp:
            page_html = resp.read().decode("utf-8", errors="ignore")

        # Extract top video ID
        vid_m = re.search(r'"videoRenderer":\{"videoId":"([a-zA-Z0-9_-]{11})"', page_html)
        if vid_m:
            result["video_id"] = vid_m.group(1)
            result["url"] = f"https://www.youtube.com/watch?v={vid_m.group(1)}&autoplay=1"

        # Extract title and channel
        title_m = re.search(r'"title":\{"runs":\[\{"text":"([^"]+)"', page_html)
        if title_m:
            result["title"] = title_m.group(1).replace(r"\u0026", "&").replace(r'\"', '"')

        channel_m = re.search(r'"ownerText":\{"runs":\[\{"text":"([^"]+)"', page_html)
        if channel_m:
            result["channel"] = channel_m.group(1).replace(r"\u0026", "&")
    except Exception:
        pass

    return result


def searchYoutube(query: str) -> str:
    """Play YouTube video or search YouTube in browser."""
    media_info = resolve_youtube_video(query)
    webbrowser.open(media_info["url"])
    return media_info["url"]


def search_wikipedia(query: str, sentences: int = 2) -> str:
    """Fetches summary from Wikipedia."""
    if not query:
        return "Please provide a topic to search on Wikipedia."
    try:
        import wikipedia
        return wikipedia.summary(query.strip(), sentences=sentences)
    except Exception:
        return f"I couldn't find a Wikipedia page for {query.strip()}."
