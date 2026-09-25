"""
Screen Vision & OCR Module for Amigo Voice Assistant.
Captures screen content, extracts visible text using native Windows OCR (winocr / pytesseract),
and answers contextual visual questions about open windows, errors, code, or documents.
"""

import asyncio
import ctypes
import logging
import os
import re
from PIL import Image

logger = logging.getLogger("amigo.screen_vision")

try:
    import pyautogui
except ImportError:
    pyautogui = None

try:
    from PIL import ImageGrab
except ImportError:
    ImageGrab = None

# winocr and pytesseract are imported lazily in read_text_from_image to prevent Direct3D/Vulkan loader conflicts


def get_active_window_title() -> str:
    """Returns the title bar text of the currently active foreground window on Windows."""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            return buf.value.strip()
    except Exception:
        pass
    return ""


def capture_screen_image(save_path: str | None = "amigo_screenshot.png"):
    """
    Captures the current desktop screen and optionally saves it as an image file.
    Uses multiple fallbacks (pyautogui -> PIL ImageGrab -> ctypes GDI) for reliability.
    """
    screenshot = None

    # Method 1: pyautogui
    if pyautogui is not None:
        try:
            screenshot = pyautogui.screenshot()
        except Exception:
            screenshot = None

    # Method 2: PIL ImageGrab
    if screenshot is None and ImageGrab is not None:
        try:
            screenshot = ImageGrab.grab(all_screens=True)
        except Exception:
            screenshot = None

    # Method 3: Windows GDI via ctypes
    if screenshot is None:
        try:
            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32

            width = user32.GetSystemMetrics(0)
            height = user32.GetSystemMetrics(1)

            hdc_screen = user32.GetDC(0)
            hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
            hbm = gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
            gdi32.SelectObject(hdc_mem, hbm)

            # Copy screen to memory DC
            gdi32.BitBlt(hdc_mem, 0, 0, width, height, hdc_screen, 0, 0, 0x00CC0020)  # SRCCOPY

            # Convert bitmap data to PIL Image
            class BITMAPINFOHEADER(ctypes.Structure):
                _fields_ = [
                    ("biSize", ctypes.c_uint32),
                    ("biWidth", ctypes.c_int32),
                    ("biHeight", ctypes.c_int32),
                    ("biPlanes", ctypes.c_uint16),
                    ("biBitCount", ctypes.c_uint16),
                    ("biCompression", ctypes.c_uint32),
                    ("biSizeImage", ctypes.c_uint32),
                    ("biXPelsPerMeter", ctypes.c_int32),
                    ("biYPelsPerMeter", ctypes.c_int32),
                    ("biClrUsed", ctypes.c_uint32),
                    ("biClrImportant", ctypes.c_uint32),
                ]

            bmi = BITMAPINFOHEADER()
            bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.biWidth = width
            bmi.biHeight = -height  # top-down
            bmi.biPlanes = 1
            bmi.biBitCount = 32
            bmi.biCompression = 0

            buffer_len = width * height * 4
            raw_data = ctypes.create_string_buffer(buffer_len)
            gdi32.GetDIBits(hdc_mem, hbm, 0, height, raw_data, ctypes.byref(bmi), 0)

            screenshot = Image.frombuffer("RGBA", (width, height), raw_data, "raw", "BGRA", 0, 1).convert("RGB")

            # Cleanup GDI handles
            gdi32.DeleteObject(hbm)
            gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(0, hdc_screen)
        except Exception:
            screenshot = None

    if screenshot is not None and save_path:
        try:
            screenshot.save(save_path)
        except Exception:
            pass

    return screenshot


def read_text_from_image(image) -> str:
    """Extracts text from a PIL Image using native Windows OCR (winocr) or pytesseract."""
    if image is None:
        return ""

    # 1. winocr (native Windows Media OCR - 100% offline, hardware-accelerated)
    try:
        import winocr
    except ImportError:
        winocr = None

    if winocr is not None:
        try:
            async def _ocr():
                res = await winocr.recognize_pil(image)
                return res.text

            text = asyncio.run(_ocr())
            if text and len(text.strip()) > 3:
                return text.strip()
        except Exception:
            pass

    # 2. pytesseract fallback
    try:
        import pytesseract
    except ImportError:
        pytesseract = None

    if pytesseract is not None:
        try:
            text = pytesseract.image_to_string(image)
            if text and len(text.strip()) > 3:
                return text.strip()
        except Exception:
            pass

    return ""


def get_screen_vision_context() -> str:
    """
    Captures the desktop screen and returns clean text extracted from the screen
    along with active window context.
    """
    img = capture_screen_image()
    if img is None:
        return ""
    text = read_text_from_image(img)
    if text:
        clean = re.sub(r"\n{3,}", "\n\n", text)
        return clean[:2500]
    return ""


def inspect_screen(question: str = "") -> dict:
    """
    Captures the desktop screen, extracts visible text and active window info,
    and returns rich multimodal vision analysis and metadata.
    """
    window_title = get_active_window_title() or "Active Window"
    save_path = "amigo_screenshot.png"
    screenshot = capture_screen_image(save_path=save_path)

    screen_text = ""
    if screenshot is not None:
        screen_text = read_text_from_image(screenshot)
        if screen_text:
            screen_text = re.sub(r"\n{3,}", "\n\n", screen_text)[:3000]

    context_parts = []
    if window_title:
        context_parts.append(f"Active Foreground Window: {window_title}")
    if screen_text:
        context_parts.append(f"Visible Screen Text & Code:\n{screen_text}")
    else:
        context_parts.append("Visual State: The screen is visible but has minimal readable text.")

    screen_context = "\n\n".join(context_parts)

    q = (question or "What is currently on my screen?").strip()
    user_prompt = (
        f"The user is asking about what is on their screen:\n\"{q}\"\n\n"
        f"Explain what is visible on the screen or in the active window, diagnose any error messages or code, "
        f"and answer directly in clear, natural English without robotic boilerplate."
    )

    reply = ""
    try:
        from ai import get_ai_response
        reply = get_ai_response(user_prompt, doc_context=screen_context)
    except Exception as e:
        reply = f"I captured your screen ({window_title}), but had trouble analyzing it: {e}"

    return {
        "reply": reply or "I inspected your screen.",
        "window_title": window_title,
        "screen_text": screen_text,
        "screenshot_path": save_path,
    }


def answer_screen_question(question: str) -> str:
    """
    High-level visual Q&A: captures screen and returns a concise spoken explanation
    tailored to the user's specific question.
    """
    res = inspect_screen(question)
    return res.get("reply", "I analyzed your screen.")


def analyze_image(image_input, question: str = "Describe what you see in this image in detail.") -> str:
    """
    Analyzes an arbitrary image file, PIL Image, or screenshot using native vision or OCR fallback.
    """
    # OCR text extraction fallback
    try:
        from PIL import Image
        img = None
        if isinstance(image_input, str) and os.path.exists(image_input):
            img = Image.open(image_input)
        elif hasattr(image_input, "save"):
            img = image_input
        if img is not None:
            ocr_text = read_text_from_image(img)
            if ocr_text:
                from local_llm import query_local_llm, sanitize_for_tts
                prompt = f"[Extracted Image Text]:\n{ocr_text[:2000]}\n\nQuestion: {question}"
                reply = query_local_llm(prompt, max_tokens=250)
                return sanitize_for_tts(reply) if reply else "I read the text in this image."
    except Exception as e:
        logger.debug(f"[Analyze Image] OCR fallback note: {e}")

    return "I was unable to analyze this image."
