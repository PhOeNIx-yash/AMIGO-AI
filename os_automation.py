import ctypes
import os
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
