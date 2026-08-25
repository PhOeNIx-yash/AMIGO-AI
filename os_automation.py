import ctypes
import os
import re
import subprocess
import time

import pyautogui

# Set failsafe so mouse can be moved to corner if needed
pyautogui.FAILSAFE = False


def type_text(text):
    """Types or pastes text into the active window/application."""
    if not text:
        return False
    try:
        time.sleep(0.1)
        try:
            import pyperclip
            pyperclip.copy(text)
            pyautogui.hotkey("ctrl", "v")
            print(f"[OS Automation] Pasted text into active window ({len(text)} chars)")
            return True
        except Exception:
            pyautogui.write(text, interval=0.005)
            print(f"[OS Automation] Typed text: {text[:50]}...")
            return True
    except Exception as e:
        print(f"[OS Automation] Error typing/pasting text: {e}")
        return False


def search_and_type(text):
    """Types text into the active input field/search bar and presses Enter."""
    try:
        time.sleep(0.1)
        pyautogui.write(text, interval=0.015)
        pyautogui.press("enter")
        print(f"[OS Automation] Searched & typed: {text}")
        return True
    except Exception as e:
        print(f"[OS Automation] Error in search_and_type: {e}")
        return False


def scroll_down(amount=600):
    """Scrolls down on the current webpage or document."""
    try:
        pyautogui.scroll(-int(amount))
        print(f"[OS Automation] Scrolled down by {amount}")
        return True
    except Exception as e:
        print(f"[OS Automation] Error scrolling down: {e}")
        return False


def scroll_up(amount=600):
    """Scrolls up on the current webpage or document."""
    try:
        pyautogui.scroll(int(amount))
        print(f"[OS Automation] Scrolled up by {amount}")
        return True
    except Exception as e:
        print(f"[OS Automation] Error scrolling up: {e}")
        return False


def new_tab(url=""):
    """Opens a new browser tab (ctrl+t) and optionally navigates to a URL."""
    try:
        pyautogui.hotkey("ctrl", "t")
        if url:
            time.sleep(0.2)
            pyautogui.write(url, interval=0.01)
            pyautogui.press("enter")
        print(f"[OS Automation] Opened new tab: {url}")
        return True
    except Exception as e:
        print(f"[OS Automation] Error opening new tab: {e}")
        return False


def close_tab():
    """Closes the current browser tab (ctrl+w)."""
    try:
        pyautogui.hotkey("ctrl", "w")
        print("[OS Automation] Closed browser tab")
        return True
    except Exception as e:
        print(f"[OS Automation] Error closing tab: {e}")
        return False


def next_tab():
    """Switches to the next browser tab (ctrl+tab)."""
    try:
        pyautogui.hotkey("ctrl", "tab")
        print("[OS Automation] Switched to next tab")
        return True
    except Exception as e:
        print(f"[OS Automation] Error switching tab: {e}")
        return False


def prev_tab():
    """Switches to the previous browser tab (ctrl+shift+tab)."""
    try:
        pyautogui.hotkey("ctrl", "shift", "tab")
        print("[OS Automation] Switched to previous tab")
        return True
    except Exception as e:
        print(f"[OS Automation] Error switching tab: {e}")
        return False





def press_shortcut(keys):
    """Presses a combination of keys (e.g. 'ctrl+c', 'alt+tab')."""
    try:
        time.sleep(0.1)
        key_list = [k.strip() for k in keys.split("+")]
        pyautogui.hotkey(*key_list)
        print(f"[OS Automation] Pressed shortcut: {keys}")
        return True
    except Exception as e:
        print(f"[OS Automation] Error pressing shortcut {keys}: {e}")
        return False


def window_action(action):
    """Performs window management actions."""
    try:
        if action == "minimize_all":
            pyautogui.hotkey("win", "d")
            print("[OS Automation] Minimized all windows")
        elif action == "close_window":
            pyautogui.hotkey("alt", "f4")
            print("[OS Automation] Closed current window")
        elif action == "switch_window":
            pyautogui.hotkey("alt", "tab")
            print("[OS Automation] Switched window")
        else:
            print(f"[OS Automation] Unknown window action: {action}")
            return False
        return True
    except Exception as e:
        print(f"[OS Automation] Error performing window action {action}: {e}")
        return False


def close_app(app_name: str = "") -> bool:
    """Closes an application by process or window title, or closes active window only if no target was named."""
    raw = (app_name or "").strip().rstrip(".!?,;:")
    clean = re.sub(r"[^\w\s-]", "", raw.lower()).strip()

    # If the user explicitly asks to close the current window/this app without naming a specific app
    if not clean or clean in (
        "current", "active", "this", "window", "app", "application",
        "current app", "current application", "active window", "this app",
        "the app", "this window", "it",
    ):
        return window_action("close_window")

    closed = False

    # 1. Close via matching running process names (e.g. systemsettings, valorant, chrome, notepad)
    try:
        import psutil
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                pname = (proc.info['name'] or '').lower()
                stem = pname.replace(".exe", "")
                if clean == stem or clean == pname or clean in stem or (len(clean) >= 4 and stem in clean):
                    proc.terminate()
                    closed = True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
        if closed:
            print(f"[OS Automation] Closed process matching '{clean}'")
    except Exception as e:
        print(f"[OS Automation] psutil close note: {e}")

    # 2. Close via matching top-level window titles (e.g. "Settings", "VALORANT", "Notepad")
    try:
        import win32gui, win32con
        def _close_win_enum(hwnd, _):
            nonlocal closed
            if win32gui.IsWindowVisible(hwnd):
                title = (win32gui.GetWindowText(hwnd) or "").lower()
                if clean in title:
                    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                    closed = True
        win32gui.EnumWindows(_close_win_enum, None)
    except Exception:
        pass

    # 3. Taskkill fallback for stubborn games / applications
    if not closed:
        try:
            import subprocess
            target = f"{clean}.exe" if not clean.endswith(".exe") else clean
            r = subprocess.run(
                ["taskkill", "/IM", target, "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2
            )
            if r.returncode == 0:
                closed = True
                print(f"[OS Automation] Taskkill closed '{target}'")
        except Exception:
            pass

    # IMPORTANT: Never send Alt+F4 when a specific app name was requested,
    # to avoid closing Amigo itself or the user's currently focused window!
    return closed


def lock_pc():
    """Locks the Windows desktop workstation."""
    try:
        ctypes.windll.user32.LockWorkStation()
        print("[OS Automation] Locked workstation")
        return True
    except Exception as e:
        print(f"[OS Automation] Error locking workstation: {e}")
        return False


def sleep_pc():
    """Puts the computer into sleep mode."""
    try:
        os.system("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")
        print("[OS Automation] System sleeping")
        return True
    except Exception as e:
        print(f"[OS Automation] Error putting system to sleep: {e}")
        return False


def empty_recycle_bin():
    """Empties the Windows Recycle Bin silently."""
    try:
        # SHERB_NOCONFIRMATION (0x1) | SHERB_NOPROGRESSUI (0x2) | SHERB_NOSOUND (0x4) = 7
        res = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 7)
        print(f"[OS Automation] Emptied Recycle Bin (result {res})")
        return True
    except Exception as e:
        print(f"[OS Automation] Error emptying Recycle Bin: {e}")
        return False


def restart_pc(delay=30):
    """Schedules a system restart."""
    try:
        os.system(f"shutdown /r /t {delay}")
        print(f"[OS Automation] Scheduled system restart in {delay} seconds")
        return True
    except Exception as e:
        print(f"[OS Automation] Error restarting system: {e}")
        return False


def cancel_shutdown():
    """Cancels a scheduled system shutdown or restart."""
    try:
        os.system("shutdown /a")
        print("[OS Automation] Cancelled shutdown/restart schedule")
        return True
    except Exception as e:
        print(f"[OS Automation] Error cancelling shutdown: {e}")
        return False


def next_track():
    """Skips to the next track/song/video globally."""
    try:
        pyautogui.press("nexttrack")
        print("[OS Automation] Skipped to next track")
        return True
    except Exception as e:
        print(f"[OS Automation] Error skipping track: {e}")
        return False


def prev_track():
    """Goes back to the previous track/song/video globally."""
    try:
        pyautogui.press("prevtrack")
        print("[OS Automation] Went back to previous track")
        return True
    except Exception as e:
        print(f"[OS Automation] Error going to previous track: {e}")
        return False


def play_pause_media():
    """Toggles play/pause media playback globally."""
    try:
        pyautogui.press("playpause")
        print("[OS Automation] Toggled play/pause media")
        return True
    except Exception as e:
        print(f"[OS Automation] Error toggling play/pause media: {e}")
        return False


def volume_up():
    """Increases system audio volume."""
    try:
        pyautogui.press("volumeup")
        print("[OS Automation] Volume up")
        return True
    except Exception as e:
        print(f"[OS Automation] Error increasing volume: {e}")
        return False


def volume_down():
    """Decreases system audio volume."""
    try:
        pyautogui.press("volumedown")
        print("[OS Automation] Volume down")
        return True
    except Exception as e:
        print(f"[OS Automation] Error decreasing volume: {e}")
        return False


def mute():
    """Toggles mute on system audio."""
    try:
        pyautogui.press("volumemute")
        print("[OS Automation] Toggled mute")
        return True
    except Exception as e:
        print(f"[OS Automation] Error toggling mute: {e}")
        return False


def set_volume(level: int | str) -> tuple[bool, str]:
    """Sets master system volume to a precise percentage (0-100) using Windows Core Audio API."""
    try:
        level_int = max(0, min(100, int(level)))
        import pythoncom
        from pycaw.pycaw import AudioUtilities
        pythoncom.CoInitialize()
        try:
            devices = AudioUtilities.GetSpeakers()
            if hasattr(devices, "EndpointVolume"):
                devices.EndpointVolume.SetMasterVolumeLevelScalar(level_int / 100.0, None)
            else:
                from ctypes import POINTER, cast
                from comtypes import CLSCTX_ALL
                from pycaw.pycaw import IAudioEndpointVolume
                interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                volume = cast(interface, POINTER(IAudioEndpointVolume))
                volume.SetMasterVolumeLevelScalar(level_int / 100.0, None)
            return True, f"Volume set to {level_int} percent."
        finally:
            pythoncom.CoUninitialize()
    except Exception as e:
        print(f"[OS Automation] Volume Error: {e}")
        return False, f"Could not adjust volume to {level} percent."


def set_brightness(level: int | str) -> tuple[bool, str]:
    """Sets monitor screen brightness (0-100) via screen_brightness_control or WMI."""
    try:
        level_int = max(0, min(100, int(level)))
        success = False
        try:
            import screen_brightness_control as sbc
            sbc.set_brightness(level_int)
            success = True
        except Exception:
            pass

        if not success:
            ps_cmd = f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1, {level_int})"
            subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, timeout=4)
            success = True

        return True, f"Screen brightness set to {level_int} percent."
    except Exception as e:
        print(f"[OS Automation] Brightness Error: {e}")
        return False, f"Could not adjust brightness to {level}."


def get_system_status() -> dict:
    """Returns real-time system hardware metrics (CPU, RAM, Battery, Plugged status)."""
    import psutil
    try:
        cpu = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        battery = psutil.sensors_battery()
        bat_percent = round(battery.percent, 1) if battery else None
        bat_status = f"{bat_percent:.0f} percent" if bat_percent is not None else "unknown"
        status_msg = f"CPU is at {cpu:.0f} percent, RAM usage is {mem.percent:.0f} percent, and Battery is at {bat_status}."
        return {
            "cpu": round(cpu, 1),
            "ram": round(mem.percent, 1),
            "battery": bat_percent,
            "plugged": battery.power_plugged if battery else None,
            "summary": status_msg,
        }
    except Exception as e:
        print(f"[OS Automation] System Status Error: {e}")
        return {
            "cpu": 0,
            "ram": 0,
            "battery": None,
            "plugged": None,
            "summary": "Could not read system hardware status.",
        }

