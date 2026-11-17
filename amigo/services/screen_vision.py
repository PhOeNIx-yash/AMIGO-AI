"""
Screen Vision & OCR Module for Amigo Voice Assistant.
Uses native Rust `roamies-hands.exe` (`amigo/services/hands.py`) for foreground window,
browser URL, UI Automation (UIA) control tree/text, screen capture, and Windows OCR.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import threading
from PIL import Image

from amigo.services import hands

logger = logging.getLogger("amigo.screen_vision")


def get_active_window_title() -> str:
    """Return the active foreground window title using native Rust `hands.front()`."""
    res = hands.front()
    if res.get("ok"):
        return str(res.get("title") or res.get("app") or "").strip()
    return ""


def wait_for_window_active(target_name: str, timeout: float = 5.0, poll_interval: float = 0.1) -> bool:
    """Wait for a window containing *target_name* in its title/app to become foreground."""
    import time
    target_lower = target_name.lower()
    start_time = time.time()

    while time.time() - start_time < timeout:
        res = hands.front()
        haystack = f"{res.get('app', '')} {res.get('title', '')}".lower()
        if target_lower in haystack:
            return True
        time.sleep(poll_interval)

    return False


def capture_screen_image(save_path: str | None = "amigo_screenshot.png", target: str = "screen"):
    """Capture the screen (or target window) via native Rust `hands.capture()` and return a PIL Image."""
    out_path = save_path
    cleanup = False
    if not out_path:
        fd, out_path = tempfile.mkstemp(suffix=".png", prefix="amigo_cap_")
        os.close(fd)
        cleanup = True

    try:
        res = hands.capture(out_path, target=target)
        if not res.get("ok") or not os.path.exists(out_path):
            return None
        with Image.open(out_path) as img:
            return img.copy()
    except Exception as exc:
        logger.debug(f"[Screen Vision] hands.capture failed: {exc}")
        return None
    finally:
        if cleanup and out_path and os.path.exists(out_path):
            try:
                os.remove(out_path)
            except OSError:
                pass


_ocr_loop: asyncio.AbstractEventLoop | None = None
_ocr_loop_lock = threading.Lock()


def _get_ocr_loop() -> asyncio.AbstractEventLoop:
    global _ocr_loop
    with _ocr_loop_lock:
        if _ocr_loop is None or _ocr_loop.is_closed():
            _ocr_loop = asyncio.new_event_loop()
        return _ocr_loop


def read_text_from_image(image) -> str:
    """Extract text from a PIL Image using native Windows Media OCR (`winocr`)."""
    if image is None:
        return ""
    try:
        import winocr
    except ImportError:
        return ""

    try:
        loop = _get_ocr_loop()
        res = loop.run_until_complete(winocr.recognize_pil(image))
        text = res.text if hasattr(res, "text") else str(res)
        return text.strip() if text and len(text.strip()) > 3 else ""
    except Exception:
        return ""


def get_screen_vision_context() -> str:
    """Return clean visible text from the active window via native Rust `hands`."""
    info = hands.describe_active_window()
    texts = info.get("texts") or ""
    if isinstance(texts, list):
        texts = "\n".join(str(t) for t in texts)
    if texts:
        return re.sub(r"\n{3,}", "\n\n", str(texts))[:2500]
    img = capture_screen_image(save_path=None)
    if img is None:
        return ""
    text = read_text_from_image(img)
    return re.sub(r"\n{3,}", "\n\n", text)[:2500] if text else ""


def inspect_screen(question: str = "") -> dict:
    """
    Inspect the active screen/window via native Rust `hands` (UIA controls, browser URL,
    foreground app/title, and screenshot capture) and answer *question*.
    """
    try:
        from amigo.services.hands import remember_target_app
        # First check what's actually in front right now
        front_res = hands.front()
        if front_res.get("ok"):
            remember_target_app(str(front_res.get("app") or ""))
    except Exception:
        pass
    hands_info = hands.describe_active_window()
    hands_used = bool(hands_info.get("ok"))
    window_title = str(hands_info.get("window_title") or "Active Window").strip()

    save_path = "amigo_screenshot.png"
    screenshot = capture_screen_image(save_path=save_path, target="front" if hands_used else "screen")

    uia_raw = hands_info.get("texts") or ""
    if isinstance(uia_raw, list):
        uia_raw = "\n".join(str(t) for t in uia_raw)
    uia_texts = str(uia_raw).strip()

    controls = hands_info.get("controls") or []
    ctrl_lines = [f"[{c.get('id')}] {c.get('role')}: {c.get('label')}" for c in controls[:25] if c.get("label")]

    ocr_text = ""
    if not uia_texts and not ctrl_lines and screenshot is not None:
        ocr_text = read_text_from_image(screenshot)
        if ocr_text:
            ocr_text = re.sub(r"\n{3,}", "\n\n", ocr_text)[:3000]

    screen_text = uia_texts[:3000] if uia_texts else ocr_text

    context_parts = []
    if window_title:
        app_name = hands_info.get("app")
        title_line = f"{app_name} - {window_title}" if app_name and app_name not in window_title else window_title
        context_parts.append(f"Active Foreground Window: {title_line}")
    if hands_info.get("url"):
        context_parts.append(f"Browser URL: {hands_info['url']}")
    if ctrl_lines:
        context_parts.append("Interactive Controls:\n" + "\n".join(ctrl_lines))
    if screen_text:
        context_parts.append(f"Visible Screen Text & Code:\n{screen_text}")
    else:
        context_parts.append("Visual State: The screen is visible but has minimal readable text.")

    screen_context = "\n\n".join(context_parts)

    q = (question or "What is currently on my screen?").strip()

    reply = ""
    try:
        from amigo.core.llm_agent import get_ai_response, sanitize_for_tts
        raw = get_ai_response(q, doc_context=screen_context, is_voice=True)
        reply = sanitize_for_tts(raw) if raw else ""
    except Exception as e:
        reply = f"I captured your screen ({window_title}), but had trouble analyzing it: {e}"
    if not reply:
        reply = f"I can see {window_title or 'your screen'}" + (f" with text: {screen_text[:200]}" if screen_text else ".")

    return {
        "reply": reply or "I inspected your screen.",
        "window_title": window_title,
        "screen_text": screen_text,
        "screenshot_path": save_path,
        "hands_used": hands_used,
        "controls": controls,
        "url": hands_info.get("url", ""),
    }


def answer_screen_question(question: str) -> str:
    """Capture/inspect the screen via `hands` and return a spoken explanation."""
    res = inspect_screen(question)
    return res.get("reply", "I analyzed your screen.")


def analyze_image(image_input, question: str = "Describe what you see in this image in detail.") -> str:
    """Analyze an arbitrary image file or PIL Image using native Windows OCR + local LLM."""
    try:
        img = None
        if isinstance(image_input, str) and os.path.exists(image_input):
            img = Image.open(image_input)
        elif hasattr(image_input, "save"):
            img = image_input
        if img is not None:
            ocr_text = read_text_from_image(img)
            if ocr_text:
                from amigo.core.llm_agent import query_local_llm, sanitize_for_tts
                prompt = f"[Extracted Image Text]:\n{ocr_text[:2000]}\n\nQuestion: {question}"
                reply = query_local_llm(prompt, max_tokens=250)
                return sanitize_for_tts(reply) if reply else "I read the text in this image."
    except Exception as e:
        logger.debug(f"[Analyze Image] OCR note: {e}")

    return "I was unable to analyze this image."
