"""
Global System-Wide Hotkey Service for Amigo Voice Assistant.
Wakes Amigo instantly across any Windows application via Alt+V.
Captures screen context before listening, allowing seamless screen vision & agentic actions
without having to open or switch to a browser window.
"""

import ctypes
import ctypes.wintypes
import logging
import os
import sys
import threading
import time

logger = logging.getLogger("amigo.hotkey_service")

# Windows Virtual-Key & Modifier Constants
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
VK_V = 0x56  # 'V' key
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
HOTKEY_ID = 1001

_hotkey_thread = None
_thread_id = None
_running = False
_is_processing = False
_lock = threading.Lock()
_broadcast_callback = None

VISION_KEYWORDS = (
    "screen", "look", "see", "this", "read", "error", "code", "window",
    "page", "image", "what is this", "what's this", "explain this", "summarize",
    "display", "active", "inspect", "app", "ui", "button", "browser"
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CUSTOM_CHIME = os.path.join(BASE_DIR, "assets", "soothing_chime.wav")
CUSTOM_OFF = os.path.join(BASE_DIR, "assets", "soothing_off.wav")

SOOTHING_WAKE_SOUNDS = [
    CUSTOM_CHIME,
    r"C:\Windows\Media\Windows Proximity Connection.wav",
    r"C:\Windows\Media\Windows Message Nudge.wav",
    r"C:\Windows\Media\Windows Background.wav",
    r"C:\Windows\Media\Windows Navigation Start.wav",
]

SOOTHING_OFF_SOUNDS = [
    CUSTOM_OFF,
    r"C:\Windows\Media\Windows Navigation Start.wav",
    r"C:\Windows\Media\Windows Background.wav",
]


def set_broadcast_callback(callback):
    """Allows ui_server to register its SSE event broadcaster directly."""
    global _broadcast_callback
    _broadcast_callback = callback


def _broadcast(event_type: str, data: dict = None):
    """Sends event to Web UI dashboard via SSE callback, in-memory reference, and HTTP bridge."""
    payload = data or {}

    # 1. Direct in-memory callback (registered via set_broadcast_callback)
    if _broadcast_callback:
        try:
            _broadcast_callback(event_type, payload)
        except Exception as e:
            logger.debug(f"[Broadcast Callback] {e}")

    # 2. Check if running inside __main__ of ui_server
    try:
        main_mod = sys.modules.get("__main__")
        if main_mod and hasattr(main_mod, "broadcaster") and main_mod.broadcaster != _broadcast_callback:
            main_mod.broadcaster.broadcast(event_type, payload)
    except Exception:
        pass

    # 3. Check if ui_server is in sys.modules
    try:
        ui_mod = sys.modules.get("ui_server")
        if ui_mod and hasattr(ui_mod, "broadcaster"):
            ui_mod.broadcaster.broadcast(event_type, payload)
    except Exception:
        pass

    # 4. HTTP Bridge to UI Server (cross-process sync to http://127.0.0.1:5000)
    def _post_http():
        try:
            import json
            import urllib.request
            req = urllib.request.Request(
                "http://127.0.0.1:5000/api/internal/broadcast",
                data=json.dumps({"type": event_type, "data": payload}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=0.8)
        except Exception:
            pass

    threading.Thread(target=_post_http, daemon=True).start()


def _play_soothing_wake_sound():
    """Plays a gentle, soothing acoustic harmonic chime."""
    try:
        import winsound
        for s in SOOTHING_WAKE_SOUNDS:
            if os.path.exists(s):
                winsound.PlaySound(s, winsound.SND_FILENAME | winsound.SND_ASYNC)
                return
    except Exception:
        pass


def _play_soothing_off_sound():
    """Plays a soft, calming completion sound."""
    try:
        import winsound
        for s in SOOTHING_OFF_SOUNDS:
            if os.path.exists(s):
                winsound.PlaySound(s, winsound.SND_FILENAME | winsound.SND_ASYNC)
                return
    except Exception:
        pass


def _is_vision_query(query: str) -> bool:
    """Detect if the spoken voice command is asking about screen content."""
    q = query.lower()
    return any(k in q for k in VISION_KEYWORDS)


def _handle_wake_action():
    """Handles the Alt+V wake event: captures screen first, updates UI state, then listens and executes."""
    global _is_processing
    with _lock:
        if _is_processing:
            logger.debug("[Hotkey] Already processing an active request, ignoring duplicate trigger.")
            return
        _is_processing = True

    try:
        # Step 1: Capture screen & active window IMMEDIATELY before any dialog or sound
        import screen_vision
        window_title = screen_vision.get_active_window_title()
        screenshot = screen_vision.capture_screen_image()
        logger.info(f"[Hotkey Wake] Screen captured. Active Window: '{window_title}'")

        # Step 2: Play soothing chime and update UI state to 'listening'
        _play_soothing_wake_sound()
        _broadcast("state_change", {"state": "listening"})

        # Step 3: Listen for user speech via microphone
        import speech_recognition as sr
        recognizer = sr.Recognizer()
        recognizer.energy_threshold = 300
        recognizer.dynamic_energy_threshold = True
        recognizer.pause_threshold = 0.75
        recognizer.phrase_threshold = 0.2

        query = ""
        try:
            with sr.Microphone() as source:
                logger.info("[Hotkey Wake] Listening for voice command...")
                audio = recognizer.listen(source, timeout=4.5, phrase_time_limit=10.0)
            from tts import transcribe_audio_data
            query = transcribe_audio_data(audio).strip()
            logger.info(f"[Hotkey Wake] User said: '{query}'")
        except sr.WaitTimeoutError:
            logger.info("[Hotkey Wake] Listening timed out (no speech detected).")
        except sr.UnknownValueError:
            logger.info("[Hotkey Wake] Speech unintelligible.")
        except Exception as e:
            logger.warning(f"[Hotkey Wake] Speech recognition note: {e}")

        # If user didn't speak, default to describing the screen
        if not query:
            query = "What is on my screen?"
            logger.info("[Hotkey Wake] Defaulting to general screen description.")

        # Update UI with user's query and set state to 'processing'
        is_vision = _is_vision_query(query)
        tool_name = "read_screen" if is_vision else "hotkey"
        _broadcast("chat_message", {
            "sender": "user",
            "text": query,
            "tool": tool_name,
        })
        _broadcast("state_change", {"state": "processing"})

        # Step 4: Dispatch Query (Vision vs General Agent Action)
        from tts import speak
        import local_llm

        reply = ""

        # Check if query is vision-oriented
        if is_vision and screenshot is not None:
            prompt = query
            if window_title:
                prompt = f"The user is viewing '{window_title}'. Question: {query}"

            # Try native Qwen 3.5 2B multimodal vision first
            reply = local_llm.query_local_vision(
                screenshot,
                prompt=prompt,
                system_prompt=(
                    "You are Amigo, a helpful voice assistant with screen vision capabilities. "
                    "Analyze the user's active computer screen and answer their question clearly and directly. "
                    "Do not use markdown, bullet points, or code formatting. Speak in natural plain English."
                ),
                max_tokens=350,
            )

            # Fallback to OCR if needed
            if not reply or len(reply.strip()) < 5:
                reply = screen_vision.answer_screen_question(query)

        # Non-vision or general agent action (apps, volume, weather, YouTube, etc.)
        if not reply:
            try:
                from local_llm import get_agent_action
                from tool_registry import execute_tool
                actions = get_agent_action(query)
                for act in actions:
                    tool = act.get("tool", "chat")
                    params = act.get("params", {})
                    spoken = act.get("speak", "")

                    if tool == "read_screen" and screenshot is not None:
                        reply = local_llm.query_local_vision(screenshot, prompt=query) or screen_vision.answer_screen_question(query)
                    else:
                        res_spoken, _, _ = execute_tool(tool, params, query, spoken)
                        reply = res_spoken or spoken

                    if reply:
                        tool_name = tool
                        break
            except Exception as e:
                logger.error(f"[Hotkey Wake] Action error: {e}")

        # Fallback to general LLM response
        if not reply:
            reply = local_llm.query_local_llm(query, max_tokens=250)

        clean_reply = local_llm.sanitize_for_tts(reply) or reply
        logger.info(f"[Hotkey Wake] Amigo responding: '{clean_reply}'")

        # Step 5: Save interaction to RAG memory (CRUCIAL for History Tab & Vector Recall)
        try:
            from ai import add_to_memory
            add_to_memory(
                user_query=query,
                assistant_reply=clean_reply,
                tool=tool_name,
            )
            logger.info("[Hotkey Wake] Interaction saved to conversation history.")
        except Exception as e:
            logger.warning(f"[Hotkey Wake] Memory save note: {e}")

        # Broadcast assistant reply to UI dashboard (triggers live message & fetchBackendHistory)
        _broadcast("chat_message", {
            "sender": "assistant",
            "text": clean_reply,
            "tool": tool_name,
            "user_query": query,
        })
        _broadcast("state_change", {"state": "speaking"})

        # Step 6: Speak output via Kokoro Neural TTS
        speak(clean_reply, block=True)

    except Exception as e:
        logger.error(f"[Hotkey Wake] Unexpected error in wake handler: {e}")
        err_msg = "I encountered an issue processing your request."
        try:
            from ai import add_to_memory
            add_to_memory(user_query=query or "Hotkey Request", assistant_reply=err_msg, tool="error")
        except Exception:
            pass
        _broadcast("chat_message", {
            "sender": "assistant",
            "text": err_msg,
            "tool": "error",
        })
        try:
            from tts import speak
            speak(err_msg)
        except Exception:
            pass
    finally:
        _play_soothing_off_sound()
        _broadcast("state_change", {"state": "idle"})
        with _lock:
            _is_processing = False


def _hotkey_message_loop():
    """Background Windows message pump for listening to Alt+V."""
    global _running, _thread_id
    _thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
    user32 = ctypes.windll.user32

    # Register Alt+V system-wide
    reg_ok = user32.RegisterHotKey(None, HOTKEY_ID, MOD_ALT | MOD_NOREPEAT, VK_V)
    if not reg_ok:
        logger.error("[Hotkey Service] Failed to register Alt+V hotkey (might be reserved by another app).")
        _running = False
        return

    logger.info("[Hotkey Service] Alt+V global wake hotkey registered successfully.")
    print(">>> [AMIGO] Alt+V Global Hotkey Active! Press Alt+V anywhere in Windows to wake Amigo. <<<", flush=True)

    msg = ctypes.wintypes.MSG()
    try:
        while _running:
            res = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if res <= 0:
                break
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                logger.info("[Hotkey Service] Alt+V triggered!")
                threading.Thread(target=_handle_wake_action, daemon=True).start()
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    finally:
        user32.UnregisterHotKey(None, HOTKEY_ID)
        logger.info("[Hotkey Service] Alt+V unregistered.")


def start_hotkey_service() -> bool:
    """Starts the global Alt+V background listener service."""
    global _hotkey_thread, _running
    if _running and _hotkey_thread and _hotkey_thread.is_alive():
        logger.info("[Hotkey Service] Already running.")
        return True

    _running = True
    _hotkey_thread = threading.Thread(target=_hotkey_message_loop, daemon=True, name="AmigoHotkeyThread")
    _hotkey_thread.start()
    return True


def stop_hotkey_service():
    """Stops the global Alt+V background listener service."""
    global _running, _thread_id
    _running = False
    if _thread_id:
        try:
            ctypes.windll.user32.PostThreadMessageW(_thread_id, WM_QUIT, 0, 0)
        except Exception:
            pass


def is_hotkey_service_running() -> bool:
    """Returns True if the Alt+V hotkey listener is active."""
    return _running and (_hotkey_thread is not None) and _hotkey_thread.is_alive()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    print("=" * 60)
    print("     AMIGO GLOBAL HOTKEY DAEMON (Alt + V)    ")
    print("=" * 60)
    print("Press Alt + V from ANY window (VS Code, Word, Chrome, etc.)")
    print("Amigo will capture screen context and listen for your voice command.")
    print("Press Ctrl + C in this terminal to exit.")
    print("=" * 60)

    start_hotkey_service()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping hotkey service...")
        stop_hotkey_service()
        print("Done.")
