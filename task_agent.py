"""
task_agent.py — Laya-Powered Autonomous Task Agent for Amigo Assistant.
Executes high-speed Windows desktop automation, browser control, and multi-step action chains.
Leverages Laya System 1 for sub-40ms decision transitions and MiniCPM for natural synthesis.
"""

import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional

import os_automation
from screen_vision import get_active_window_title
import tool_registry
from laya_router import route_intent_via_laya, extract_parameters_and_tool

logger = logging.getLogger("amigo.task_agent")

# Strong sequential conjunctions that inherently divide multi-action commands
_RE_SEQUENTIAL_CONJ = re.compile(
    r"\s*(?:;\s*|\band\s+then\b|\bthen\b|\bafter\s+that\b|\band\s+also\b|\bbut\s+also\b|\bas\s+well\s+as\b)\s*",
    re.IGNORECASE,
)

# Common command imperative verbs that indicate the start of a distinct action clause
_RE_ACTION_START = re.compile(
    r"^(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?(?:also\s+)?"
    r"(?:open|launch|start|run|close|quit|exit|kill|play|stream|listen|watch|replay|repeat|search|google|look\s+up|find|type|write|input|press|hit|click|scroll|set|turn|mute|unmute|lock|sleep|restart|reboot|check|show|tell|read|summarize)\b",
    re.IGNORECASE,
)

_RE_STEP_CLEANUP = re.compile(
    r"^(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?(?:also\s+|and\s+)?",
    re.IGNORECASE,
)




# ---------------------------------------------------------------------------
# 1. Desktop & Active App Grounding (Feature 1)
# ---------------------------------------------------------------------------

def get_desktop_context() -> Dict[str, Any]:
    """Inspects active desktop environment for context-aware automation."""
    active_win = get_active_window_title() or "Desktop"
    status = os_automation.get_system_status()
    return {
        "active_window": active_win,
        "cpu_percent": status.get("cpu", 0),
        "ram_percent": status.get("ram", 0),
        "battery": status.get("battery"),
    }


def execute_desktop_action(action: str, params: Dict[str, Any]) -> tuple[bool, str]:
    """Executes atomic Windows desktop interactions."""
    try:
        if action == "type_text":
            text = params.get("text", "")
            target_app = params.get("app", "")
            if target_app:
                from app_opener import open_windows_app
                open_windows_app(target_app)
                time.sleep(0.3)
            ok = os_automation.type_text(text)
            return ok, f"Typed text into {target_app or 'active window'}." if ok else "Failed to type text."

        elif action == "press_key":
            keys = params.get("keys", "")
            key_map = {
                "enter": "enter", "return": "enter",
                "save": "ctrl+s", "copy": "ctrl+c", "paste": "ctrl+v",
                "select all": "ctrl+a", "undo": "ctrl+z",
                "escape": "esc", "esc": "esc", "tab": "tab",
                "space": "space", "backspace": "backspace",
            }
            resolved_key = key_map.get(keys.lower().strip(), keys)
            ok = os_automation.press_shortcut(resolved_key)
            return ok, f"Pressed {resolved_key}." if ok else f"Failed to press {keys}."

        elif action == "window_management":
            sub_act = params.get("action", "minimize_all")
            ok = os_automation.window_action(sub_act)
            return ok, f"Executed window action: {sub_act}."

        elif action == "click_screen":
            import pyautogui
            x = params.get("x")
            y = params.get("y")
            if x is not None and y is not None:
                pyautogui.click(int(x), int(y))
                return True, f"Clicked at ({x}, {y})."
            pyautogui.click()
            return True, "Clicked active element."

    except Exception as e:
        logger.error("[Desktop Action Error]: %s", e)
        return False, f"Desktop action error: {e}"

    return False, "Unknown desktop action."


# ---------------------------------------------------------------------------
# 2. Web Page Navigation & Browser Control (Feature 2)
# ---------------------------------------------------------------------------

def execute_browser_action(action: str, params: Dict[str, Any]) -> tuple[bool, str]:
    """Executes high-speed browser tab and page interactions."""
    try:
        if action == "new_tab":
            url = params.get("url", "")
            ok = os_automation.new_tab(url)
            return ok, f"Opened new tab{' for ' + url if url else ''}."

        elif action == "close_tab":
            ok = os_automation.close_tab()
            return ok, "Closed browser tab."

        elif action == "next_tab":
            ok = os_automation.next_tab()
            return ok, "Switched to next tab."

        elif action == "prev_tab":
            ok = os_automation.prev_tab()
            return ok, "Switched to previous tab."

        elif action == "scroll_down":
            amount = int(params.get("amount", 600))
            ok = os_automation.scroll_down(amount)
            return ok, f"Scrolled down by {amount}."

        elif action == "scroll_up":
            amount = int(params.get("amount", 600))
            ok = os_automation.scroll_up(amount)
            return ok, f"Scrolled up by {amount}."

        elif action == "search_and_type":
            text = params.get("text", "")
            ok = os_automation.search_and_type(text)
            return ok, f"Searched: {text}"

        elif action == "open_website":
            url = params.get("url", "")
            import webbrowser
            target = url if url.startswith("http") else f"https://{url}"
            webbrowser.open(target)
            return True, f"Opened {target}."

    except Exception as e:
        logger.error("[Browser Action Error]: %s", e)
        return False, f"Browser action error: {e}"

    return False, "Unknown browser action."


# ---------------------------------------------------------------------------
# 3. Task Planning & Step Decomposition (Feature 3)
# ---------------------------------------------------------------------------

def decompose_task(user_prompt: str) -> List[str]:
    """Decomposes a compound voice command into sequential action steps without splitting natural phrases."""
    clean = (user_prompt or "").strip()
    if not clean:
        return []

    # 1. Split on explicit sequential markers (; then, and then, after that, and also, but also, as well as)
    initial_segments = [s.strip().rstrip(".!?,") for s in _RE_SEQUENTIAL_CONJ.split(clean) if s and s.strip()]
    if not initial_segments:
        initial_segments = [clean]

    # 2. For segments containing 'and' or '&', only split if the clause after 'and' starts with an imperative action verb
    refined_steps: List[str] = []
    for seg in initial_segments:
        parts = re.split(r"\s+(?:and|&)\s+", seg, flags=re.IGNORECASE)
        if len(parts) > 1:
            current = parts[0].strip()
            for following in parts[1:]:
                following_clean = following.strip()
                if _RE_ACTION_START.search(following_clean):
                    if current:
                        refined_steps.append(current)
                    current = following_clean
                else:
                    # 'and' is part of a phrase (e.g. 'bed and breakfast', 'rock and roll', 'tom and jerry')
                    current = f"{current} and {following_clean}"
            if current:
                refined_steps.append(current)
        else:
            refined_steps.append(seg)

    # 3. Validation: a multi-step decomposition is valid only if each step has substance and an action directive
    if len(refined_steps) > 1:
        cleaned_steps = [_RE_STEP_CLEANUP.sub("", s).strip() for s in refined_steps if s.strip()]
        if len(cleaned_steps) > 1 and all(
            len(s.split()) >= 2 and (_RE_ACTION_START.search(s) or any(w in s.lower() for w in ("weather", "time", "date", "battery")))
            for s in cleaned_steps
        ):
            return cleaned_steps

    return [clean]



def resolve_step_intent(
    step: str,
    context: Dict[str, Any],
    conversation_history: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Resolves single action step in a multi-step chain using Laya System 1."""
    laya_act = route_intent_via_laya(step, conversation_history=conversation_history)
    if laya_act and laya_act.get("tool") not in ("chat", None):
        laya_act["step_text"] = step
        if laya_act.get("tool") == "type_text" and not laya_act.get("params", {}).get("app"):
            if last_app := context.get("last_opened_app"):
                laya_act.setdefault("params", {})["app"] = last_app
                laya_act["speak"] = f"Typing text into {last_app}."
        return laya_act

    return {"tool": "chat", "params": {}, "step_text": step}



# ---------------------------------------------------------------------------
# 4. Autonomous Action Chain Execution Loop (Zero Latency)
# ---------------------------------------------------------------------------

def execute_action_chain(
    steps: List[str],
    on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Executes multi-step action sequences autonomously with sub-40ms step transitions.
    Propagates intermediate context (e.g. opened apps, file paths) across steps.
    """
    results = []
    if context is None:
        context = get_desktop_context()
    total_steps = len(steps)

    logger.info("[Task Agent] Executing chain of %d steps", total_steps)

    for idx, step_text in enumerate(steps, 1):
        step_started = time.perf_counter()
        action_plan = resolve_step_intent(step_text, context, conversation_history=conversation_history)
        tool = action_plan.get("tool", "chat")
        params = action_plan.get("params", {})

        if on_progress:
            try:
                on_progress({
                    "step": idx,
                    "total": total_steps,
                    "status": "executing",
                    "step_text": step_text,
                    "tool": tool,
                })
            except Exception:
                pass

        step_result_msg = ""
        success = True

        # Dispatch: Desktop Automation
        if tool in ("type_text", "press_key", "window_management", "click_screen"):
            success, step_result_msg = execute_desktop_action(tool, params)

        # Dispatch: Browser & Web Navigation
        elif tool in ("new_tab", "close_tab", "next_tab", "prev_tab", "scroll_down", "scroll_up", "search_and_type", "open_website"):
            success, step_result_msg = execute_browser_action(tool, params)

        # Dispatch: Native Amigo Tool Registry
        else:
            try:
                spoken_res, _, _ = tool_registry.execute_tool(tool, params, query=step_text)
                step_result_msg = spoken_res or f"Executed {tool.replace('_', ' ')}."
            except Exception as e:
                success = False
                step_result_msg = f"Error in {tool}: {e}"

        # Context updates for subsequent steps
        if tool == "open_app":
            app_name = params.get("name", "")
            if app_name:
                context["last_opened_app"] = app_name
                time.sleep(0.25)  # Allow Windows a brief moment to focus new app window

        elapsed_ms = (time.perf_counter() - step_started) * 1000
        logger.info(
            "[Task Agent] Step %d/%d '%s' -> tool=%s (%.1f ms): %s",
            idx, total_steps, step_text, tool, elapsed_ms, step_result_msg,
        )

        results.append({
            "step": idx,
            "text": step_text,
            "tool": tool,
            "success": success,
            "message": step_result_msg,
            "duration_ms": elapsed_ms,
        })

    # Generate synthesized summary of completion
    completed_steps = [r["message"] for r in results if r["message"]]
    summary = " ".join(completed_steps[:2]) if completed_steps else "Completed all requested actions."

    return {
        "status": "success" if all(r["success"] for r in results) else "partial",
        "total_steps": total_steps,
        "steps": results,
        "summary": summary,
        "context": context,
    }


def run_task(
    user_prompt: str,
    on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Unified entry point: decomposes, plans, and executes multi-step task."""
    steps = decompose_task(user_prompt)
    if not steps:
        return {"status": "empty", "summary": "No actionable steps identified."}
    return execute_action_chain(steps, on_progress=on_progress, conversation_history=conversation_history)
