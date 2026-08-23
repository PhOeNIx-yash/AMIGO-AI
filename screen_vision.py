"""
Screen Vision & OCR Module for Amigo Voice Assistant.
Captures screen content, extracts visible text using native Windows OCR (winocr / pytesseract),
and answers contextual visual questions about open windows, errors, code, or documents.
"""

import asyncio
import ctypes
import os
import re
from PIL import Image

try:
    import pyautogui
except ImportError:
    pyautogui = None

try:
    from PIL import ImageGrab
except ImportError:
    ImageGrab = None

try:
    import winocr
except ImportError:
    winocr = None

try:
    import pytesseract
except ImportError:
    pytesseract = None


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


def answer_screen_question(question: str) -> str:
    """
    High-level visual Q&A: captures screen, extracts visible text & active window,
    and returns a concise spoken explanation tailored to the user's specific question.
    """
    window_title = get_active_window_title()
    screen_text = get_screen_vision_context()

    if not screen_text and not window_title:
        return "I captured the screen, but could not detect readable text in the current window."

    context_parts = []
    if window_title:
        context_parts.append(f"Active Window: {window_title}")
    if screen_text:
        context_parts.append(f"Visible Screen Content:\n{screen_text}")

    screen_context = "\n\n".join(context_parts)

    system_prompt = (
        "You are Amigo, a helpful voice assistant with screen vision capabilities. "
        "You are given text extracted from the user's active computer screen. "
        "Answer the user's question directly, clearly, and concisely in 1-3 spoken sentences. "
        "Do not use markdown, bullet points, or code formatting. Speak in natural plain English."
    )

    user_prompt = (
        f"[Screen Context]:\n{screen_context}\n\n"
        f"User Question: {question if question else 'What is currently on my screen?'}"
    )

    try:
        from local_llm import query_local_llm, sanitize_for_tts
        reply = query_local_llm(user_prompt, system_prompt=system_prompt, max_tokens=300)
        return sanitize_for_tts(reply) if reply else "I analyzed the screen, but have nothing further to report."
    except Exception as e:
        return f"I had trouble analyzing the screen content: {e}"
