import html
import re
import urllib.parse
import urllib.request
import webbrowser


def clean_search_query(raw_query: str) -> str:
    """Extract core search terms from natural conversational questions."""
    if not raw_query:
        return ""
    cleaned = re.sub(
        r"^(?:hey\s+amigo|amigo|hi\s+amigo|hello\s+amigo|can\s+you\s+(?:please\s+)?(?:tell\s+me|show\s+me|find|search|look\s+up|explain)|could\s+you\s+(?:please\s+)?(?:tell\s+me|show\s+me|find|search|look\s+up|explain)|please\s+(?:tell\s+me|show\s+me|find|search|look\s+up|explain)|tell\s+me\s+(?:about\s+)?|who\s+(?:is|was|are|were|the)\s+|what\s+(?:is|was|are|were|the)\s+|when\s+(?:is|was|did)\s+|where\s+(?:is|was|are|were)\s+|how\s+(?:to|does|is)\s+|search\s+(?:for\s+|google\s+for\s+|on\s+google\s+for\s+)?|google\s+)",
        "",
        raw_query.strip(),
        flags=re.IGNORECASE,
    ).strip()
    cleaned = re.sub(r"\s+(?:please|for\s+me|right\s+now|now|today|online)$", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned if len(cleaned) > 2 else raw_query.strip()


def scrape_web_info(query: str) -> str:
    """
    Performs a fast background web search to extract real-time snippets
    without opening browser windows.
    Returns a clean string summary of web search results.
    """
    if not query or not query.strip():
        return ""

    core_q = clean_search_query(query)
    search_candidates = [q for q in [core_q[:120], query[:120]] if q]

    # 1. DuckDuckGo POST Endpoint (Bypasses GET anomaly/bot checks reliably)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": "https://html.duckduckgo.com/",
        "Origin": "https://html.duckduckgo.com",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    for q in search_candidates:
        try:
            url = "https://html.duckduckgo.com/html/"
            data = urllib.parse.urlencode({"q": q, "b": ""}).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=4) as resp:
                raw_html = resp.read().decode("utf-8", errors="ignore")

            snippets = re.findall(
                r'<a class="result__snippet[^>]*>(.*?)</a>', raw_html, re.DOTALL
            )
            if not snippets:
                snippets = re.findall(
                    r'<td class="result-snippet"[^>]*>(.*?)</td>', raw_html, re.DOTALL
                )

            clean_snippets = []
            for s in snippets[:4]:
                text = re.sub(r"<[^>]+>", " ", s)
                text = html.unescape(text)
                text = re.sub(r"\s+", " ", text).strip()
                if text and len(text) > 15:
                    clean_snippets.append(text)

            if clean_snippets:
                return " ".join(clean_snippets)[:1800]
        except Exception:
            continue

    # 2. Wikipedia Search API Fallback (with term relevance verification)
    try:
        import json
        url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={urllib.parse.quote(core_q)}&format=json&utf8=1&srlimit=3"
        req = urllib.request.Request(url, headers={"User-Agent": "AmigoVoiceAssistant/1.0 (desktop app)"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            search_results = data.get("query", {}).get("search", [])
            wiki_snippets = []
            core_words = [w.lower() for w in re.findall(r"\w+", core_q) if len(w) > 3]
            for r in search_results:
                title = r.get("title", "")
                snippet_raw = r.get("snippet", "")
                clean = re.sub(r"<[^>]+>", " ", snippet_raw)
                clean = html.unescape(clean)
                clean = re.sub(r"\s+", " ", clean).strip()
                combined_text = (title + " " + clean).lower()
                if not core_words or any(w in combined_text for w in core_words):
                    wiki_snippets.append(f"{title}: {clean}")
            if wiki_snippets:
                return " ".join(wiki_snippets)[:1500]
    except Exception:
        pass

    return ""


def searchGoogle(query):
    """Search Google with the provided query."""
    if not query:
        return
    clean_q = re.sub(
        r"^(show me|can you show me|please|open|search|find|look up|what|which)\s*",
        "",
        query,
        flags=re.IGNORECASE,
    ).strip()
    clean_q = clean_q.replace("google", "").strip()
    if not clean_q:
        clean_q = query
    webbrowser.open(f"https://www.google.com/search?q={urllib.parse.quote(clean_q)}")


def searchYoutube(query):
    """Search YouTube with the provided query."""
    query = query.replace("youtube", "").strip()
    webbrowser.open(f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}")
