"""
Autonomous Computer-Use Agent (CUA) Service for Amigo.
Directly connects to and drives the official CUA Driver (trycua/cua) runtime on Windows.
Enables autonomous GUI interaction, accessibility inspection, non-intrusive cursor
overlay injection, app control, and intelligent multi-step desktop automation.
"""

import asyncio
import inspect
import json
import logging
import os
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

logger = logging.getLogger("amigo.cua_agent")

# Attempt importing the official CUA driver and CUA SDK
_CUA_DRIVER_AVAILABLE = False
_CUA_SDK_AVAILABLE = False

try:
    import cua_driver
    _CUA_DRIVER_AVAILABLE = True
except ImportError:
    cua_driver = None

try:
    import cua
    import cua_agent
    _CUA_SDK_AVAILABLE = True
except ImportError:
    cua = None
    cua_agent = None


class CuaDriverBridge:
    """
    Singleton bridge connecting Amigo directly to the official CUA Driver instance.
    Provides thread-safe access to CUA Driver tools, sessions, and native inputs.
    """
    _instance: Optional["CuaDriverBridge"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._driver: Optional[Any] = None
        self._session_id: Optional[str] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._error: Optional[str] = None
        self._init_bridge()

    @classmethod
    def get_instance(cls) -> "CuaDriverBridge":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _init_bridge(self):
        """Spawns an internal asyncio worker loop to drive native CUA coroutines."""
        if not _CUA_DRIVER_AVAILABLE:
            self._error = "cua-driver package is not installed."
            self._ready.set()
            return

        def _worker():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                # Instantiate official CUA Driver
                self._driver = cua_driver.CuaDriver.create()
                # Start default trusted session
                start_input = cua_driver.StartSessionInput(
                    session=None,
                    capture_scope=None,
                    cursor_theme=None,
                    cursor_motion=None,
                )
                start_task = self._driver.start_session(start_input)
                start_res = self._loop.run_until_complete(start_task)
                if hasattr(start_res, "state") and hasattr(start_res.state, "session"):
                    self._session_id = start_res.state.session
                else:
                    self._session_id = "implicit"
                logger.info(f"[CUA Driver] Connected successfully. Active session: {self._session_id}")
            except Exception as e:
                logger.error(f"[CUA Driver] Initialization failed: {e}", exc_info=True)
                self._error = str(e)
            finally:
                self._ready.set()

            self._loop.run_forever()

        self._thread = threading.Thread(target=_worker, name="CuaDriverWorker", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=10.0)

    @property
    def is_available(self) -> bool:
        return _CUA_DRIVER_AVAILABLE and self._driver is not None

    def get_status(self) -> Dict[str, Any]:
        return {
            "driver_installed": _CUA_DRIVER_AVAILABLE,
            "sdk_installed": _CUA_SDK_AVAILABLE,
            "connected": self.is_available,
            "session_id": self._session_id,
            "error": self._error,
            "driver_version": getattr(cua_driver, "__version__", "0.34.0") if _CUA_DRIVER_AVAILABLE else None,
            "binary_path": str(cua_driver.get_binary_path()) if _CUA_DRIVER_AVAILABLE and hasattr(cua_driver, "get_binary_path") else None,
        }

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None, timeout: float = 25.0) -> Dict[str, Any]:
        """
        Executes any tool exposed by the official CUA driver.
        Returns standard dictionary with success status, text output, and structured JSON.
        """
        if not self.is_available or not self._loop:
            return {
                "status": "error",
                "error": self._error or "CUA Driver is not available or disconnected.",
                "text": "CUA Driver is not connected.",
            }

        arguments = arguments or {}
        arg_json = json.dumps(arguments)

        async def _call():
            raw_res = await self._driver.call_tool(name, arg_json)
            # Safely extract text and metadata
            text_val = getattr(raw_res, "text", "") or ""
            is_err = getattr(raw_res, "is_error", False)
            struct_val = getattr(raw_res, "structured_json", None)
            err_code = getattr(raw_res, "error_code", None)
            return {
                "status": "error" if is_err else "success",
                "text": text_val,
                "structured": struct_val,
                "error_code": err_code,
            }

        fut = asyncio.run_coroutine_threadsafe(_call(), self._loop)
        try:
            return fut.result(timeout=timeout)
        except Exception as e:
            logger.error(f"[CUA Driver] Error calling tool '{name}': {e}")
            return {
                "status": "error",
                "error": str(e),
                "text": f"Failed executing {name}: {e}",
            }

    # -------------------------------------------------------------------------
    # High-level direct operations powered by CUA Driver
    # -------------------------------------------------------------------------

    def get_screen_size(self) -> Dict[str, Any]:
        res = self.call_tool("get_screen_size", {})
        val = res.get("structured")
        if val:
            if isinstance(val, str):
                try:
                    return json.loads(val)
                except Exception:
                    pass
            elif isinstance(val, dict):
                return val
        return {"width": 1920, "height": 1080}

    def get_cursor_position(self) -> Dict[str, Any]:
        res = self.call_tool("get_cursor_position", {})
        val = res.get("structured")
        if val:
            if isinstance(val, str):
                try:
                    return json.loads(val)
                except Exception:
                    pass
            elif isinstance(val, dict):
                return val
        return {"x": 0, "y": 0}

    def move_cursor(self, x: float, y: float) -> Dict[str, Any]:
        return self.call_tool("move_cursor", {"x": float(x), "y": float(y)})

    def click(self, x: Optional[float] = None, y: Optional[float] = None, button: str = "left", count: int = 1) -> Dict[str, Any]:
        args: Dict[str, Any] = {"button": button.upper(), "count": count}
        if x is not None and y is not None:
            args["x"] = float(x)
            args["y"] = float(y)
        return self.call_tool("click", args)

    def double_click(self, x: Optional[float] = None, y: Optional[float] = None) -> Dict[str, Any]:
        return self.click(x=x, y=y, count=2)

    def right_click(self, x: Optional[float] = None, y: Optional[float] = None) -> Dict[str, Any]:
        return self.click(x=x, y=y, button="right")

    def type_text(self, text: str, pid: Optional[int] = None) -> Dict[str, Any]:
        args: Dict[str, Any] = {"text": text}
        if pid is not None:
            args["pid"] = pid
        else:
            args["scope"] = "desktop"
        return self.call_tool("type_text", args)

    def press_key(self, key: str, modifiers: Optional[List[str]] = None, pid: Optional[int] = None) -> Dict[str, Any]:
        args: Dict[str, Any] = {"key": key}
        if modifiers:
            args["modifiers"] = modifiers
        if pid is not None:
            args["pid"] = pid
        else:
            args["scope"] = "desktop"
        return self.call_tool("press_key", args)

    def hotkey(self, keys: Union[str, List[str]], pid: Optional[int] = None) -> Dict[str, Any]:
        if isinstance(keys, str):
            key_list = [k.strip() for k in keys.split("+")]
        else:
            key_list = keys
        args: Dict[str, Any] = {"keys": key_list}
        if pid is not None:
            args["pid"] = pid
        else:
            args["scope"] = "desktop"
        return self.call_tool("hotkey", args)

    def scroll(self, x: float = 0, y: float = 0, direction: str = "DOWN", amount: int = 2, pid: Optional[int] = None) -> Dict[str, Any]:
        args: Dict[str, Any] = {
            "x": float(x),
            "y": float(y),
            "direction": direction.upper(),
            "amount": amount,
        }
        if pid is not None:
            args["pid"] = pid
        else:
            args["scope"] = "desktop"
        return self.call_tool("scroll", args)

    def list_apps(self) -> Dict[str, Any]:
        return self.call_tool("list_apps", {})

    def list_windows(self) -> Dict[str, Any]:
        return self.call_tool("list_windows", {})

    def launch_app(self, name: str) -> Dict[str, Any]:
        return self.call_tool("launch_app", {"name": name})

    def kill_app(self, name: str) -> Dict[str, Any]:
        return self.call_tool("kill_app", {"name": name})

    def clipboard_read(self) -> Dict[str, Any]:
        return self.call_tool("clipboard_read", {})

    def clipboard_write(self, text: str) -> Dict[str, Any]:
        return self.call_tool("clipboard_write", {"text": text})

    # -------------------------------------------------------------------------
    # Advanced CUA Driver Capabilities
    # -------------------------------------------------------------------------

    def get_accessibility_tree(self) -> Dict[str, Any]:
        """Reads complete Windows UI Automation (UIA) tree across desktop and apps."""
        return self.call_tool("get_accessibility_tree", {})

    def configure_cursor_motion(
        self,
        style: str = "signature_arc",
        glow: bool = True,
        trail: bool = True,
        glide_duration_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Configures animated agent cursor overlay physics (signature_arc, spring_settle, comet_swoop)."""
        args: Dict[str, Any] = {
            "style": style,
            "effects": {"glow": glow, "trail": trail},
        }
        if glide_duration_ms:
            args["glide_duration_ms"] = glide_duration_ms
        return self.call_tool("set_agent_cursor_motion", args)

    def set_value(self, value: str, element_token: Optional[str] = None, pid: Optional[int] = None) -> Dict[str, Any]:
        """Injects text directly into UI Automation input elements without key simulation."""
        args: Dict[str, Any] = {"value": value}
        if element_token:
            args["element_token"] = element_token
        if pid:
            args["pid"] = pid
        return self.call_tool("set_value", args)

    def verify_state(self, expect: Dict[str, Any], timeout_ms: int = 5000, pid: Optional[int] = None) -> Dict[str, Any]:
        """Verifies preconditions or postconditions of the desktop/window state."""
        args: Dict[str, Any] = {"expect": expect, "timeout_ms": timeout_ms}
        if pid:
            args["pid"] = pid
        return self.call_tool("verify_state", args)

    def start_recording(self, output_dir: Optional[str] = None, record_video: bool = True) -> Dict[str, Any]:
        """Starts video recording and trajectory tracing of agent actions."""
        args: Dict[str, Any] = {"record_video": record_video}
        if output_dir:
            args["output_dir"] = output_dir
        return self.call_tool("start_recording", args)

    def stop_recording(self) -> Dict[str, Any]:
        """Stops the active recording session and finalizes video/trajectory."""
        return self.call_tool("stop_recording", {})

    def get_recording_state(self) -> Dict[str, Any]:
        """Checks if a recording session is currently capturing."""
        return self.call_tool("get_recording_state", {})

    def zoom(self, x1: float, y1: float, x2: float, y2: float, pid: Optional[int] = None) -> Dict[str, Any]:
        """High-precision zoom inspection into a sub-region of the screen or window."""
        args: Dict[str, Any] = {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
        if pid:
            args["pid"] = pid
        return self.call_tool("zoom", args)

    def browser_navigate(self, url: str) -> Dict[str, Any]:
        """Direct Chromium DevTools Protocol (CDP) browser navigation."""
        return self.call_tool("browser_navigate", {"url": url})

    def get_browser_state(self) -> Dict[str, Any]:
        """Queries browser tab and DOM state via CDP."""
        return self.call_tool("get_browser_state", {})


class AutonomousComputerUseAgent:
    """
    Autonomous Computer-Use Agent coordinating high-level natural language goals
    into multi-step GUI actions executed exclusively through the official CUA driver.
    """

    def __init__(self, driver_bridge: Optional[CuaDriverBridge] = None):
        self.bridge = driver_bridge or CuaDriverBridge.get_instance()

    def run_task(
        self,
        task: str,
        max_steps: int = 8,
        progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """
        Executes an autonomous computer-use task.
        Uses Amigo's intelligence to plan and dispatch actions to the CUA driver.
        """
        if not self.bridge.is_available:
            return {
                "status": "error",
                "summary": "CUA Driver is not available or disconnected.",
                "steps": [],
            }

        # Lazy import of LLM agent response generator to avoid circular dependencies
        try:
            from amigo.core.llm_agent import get_ai_response
        except Exception:
            get_ai_response = None

        logger.info(f"[CUA Agent] Starting autonomous task: {task!r}")
        steps_log: List[Dict[str, Any]] = []

        # Step 0: Initial environment perception via CUA Driver
        screen_info = self.bridge.get_screen_size()
        cursor_pos = self.bridge.get_cursor_position()

        initial_state = {
            "screen_width": screen_info.get("width", 1920),
            "screen_height": screen_info.get("height", 1080),
            "cursor": cursor_pos,
        }

        if progress_cb:
            progress_cb({
                "type": "cua_started",
                "task": task,
                "initial_state": initial_state,
                "timestamp": time.time(),
            })

        # Planner system prompt instructing the model to emit CUA Driver action calls
        system_prompt = (
            "You are the Amigo Autonomous Computer-Use Agent controlling the Windows desktop via official CUA Driver.\n"
            "Your available CUA Driver tools are:\n"
            "- launch_app: {\"name\": string}\n"
            "- click: {\"x\": number, \"y\": number, \"button\": \"LEFT\"|\"RIGHT\"}\n"
            "- double_click: {\"x\": number, \"y\": number}\n"
            "- type_text: {\"text\": string}\n"
            "- set_value: {\"value\": string, \"element_token\": string}\n"
            "- press_key: {\"key\": string}\n"
            "- hotkey: {\"keys\": [\"key1\", \"key2\"]}\n"
            "- scroll: {\"direction\": \"DOWN\"|\"UP\", \"amount\": number}\n"
            "- move_cursor: {\"x\": number, \"y\": number}\n"
            "- get_accessibility_tree: {}\n"
            "- browser_navigate: {\"url\": string}\n"
            "- verify_state: {\"expect\": {...}}\n"
            "- zoom: {\"x1\": number, \"y1\": number, \"x2\": number, \"y2\": number}\n"
            "- finish: {\"summary\": string}\n\n"
            "Respond ONLY with a single JSON object specifying the next tool to run, e.g.:\n"
            "{\"action\": \"tool_name\", \"params\": {...}, \"thought\": \"reasoning\"}\n"
            f"Screen Resolution: {initial_state['screen_width']}x{initial_state['screen_height']}.\n"
            "When the overall task is fulfilled or when you have performed the requested actions, select action 'finish'."
        )

        history_summary: List[str] = []
        final_summary = ""

        for step_idx in range(1, max_steps + 1):
            prompt = (
                f"GOAL: {task}\n"
                f"STEP: {step_idx}/{max_steps}\n"
                f"PREVIOUS ACTIONS:\n" + ("\n".join(history_summary) if history_summary else "None yet.\n") +
                "\nProvide the next single CUA action as JSON."
            )

            raw_plan = ""
            if get_ai_response:
                try:
                    full_query = f"{system_prompt}\n\nTask:\n{prompt}"
                    raw_plan = get_ai_response(
                        full_query,
                        use_memory=False,
                        is_voice=False,
                    )
                except Exception as e:
                    logger.error(f"[CUA Agent] LLM planning failed: {e}")
                    raw_plan = ""

            action_data = self._parse_action_response(raw_plan, task, step_idx)
            action_name = action_data.get("action", "").lower().strip()
            params = action_data.get("params", {})
            thought = action_data.get("thought", "")

            step_entry = {
                "step": step_idx,
                "action": action_name,
                "params": params,
                "thought": thought,
                "timestamp": time.time(),
            }

            if action_name == "finish" or not action_name:
                final_summary = params.get("summary") or thought or f"Completed autonomous execution for '{task}'."
                step_entry["result"] = {"status": "success", "text": final_summary}
                steps_log.append(step_entry)
                if progress_cb:
                    progress_cb({"type": "cua_step", "step": step_entry})
                break

            # Execute the action via CUA Driver
            cua_result = self._dispatch_cua_action(action_name, params)
            step_entry["result"] = cua_result
            steps_log.append(step_entry)

            history_summary.append(
                f"- Step {step_idx}: {action_name}({json.dumps(params)}) -> {cua_result.get('status')}: {cua_result.get('text', '')[:60]}"
            )

            if progress_cb:
                progress_cb({"type": "cua_step", "step": step_entry})

            # Small delay to let the OS GUI render/react
            time.sleep(0.4)

        if not final_summary:
            final_summary = f"Finished autonomous execution of {len(steps_log)} steps for: {task}"

        report = {
            "status": "completed",
            "task": task,
            "steps": steps_log,
            "summary": final_summary,
            "driver_status": self.bridge.get_status(),
        }

        if progress_cb:
            progress_cb({"type": "cua_finished", "report": report})

        logger.info(f"[CUA Agent] Task complete. {len(steps_log)} steps executed.")
        return report

    def _parse_action_response(self, text: str, task: str, step_idx: int) -> Dict[str, Any]:
        """Safely parses JSON tool actions from LLM output with deterministic fallbacks."""
        if text:
            # Strip think tags or markdown blocks
            clean = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.I).strip()
            clean = re.sub(r"```json\s*", "", clean)
            clean = re.sub(r"```\s*", "", clean).strip()

            # Attempt finding JSON object
            match = re.search(r"\{[\s\S]*?\}", clean)
            if match:
                try:
                    obj = json.loads(match.group(0))
                    if "action" in obj:
                        return obj
                    if "tool" in obj:
                        return {
                            "action": obj["tool"],
                            "params": obj.get("params") or obj.get("arguments") or {},
                            "thought": obj.get("thought", ""),
                        }
                except Exception:
                    pass

        # Deterministic single-step fallback if LLM is unavailable or non-JSON
        lower_task = task.lower()
        if step_idx == 1:
            if "open" in lower_task or "launch" in lower_task:
                target = re.sub(r"^(?:please\s+)?(?:open|launch|start|run)\s+", "", task, flags=re.I).strip()
                return {"action": "launch_app", "params": {"name": target}, "thought": f"Launching {target} via CUA Driver"}
            if "type" in lower_task or "write" in lower_task:
                content = re.sub(r"^(?:please\s+)?(?:type|write)\s+", "", task, flags=re.I).strip()
                return {"action": "type_text", "params": {"text": content}, "thought": f"Typing text via CUA Driver"}
            if "press" in lower_task or "key" in lower_task:
                return {"action": "hotkey", "params": {"keys": ["enter"]}, "thought": "Sending keystroke via CUA Driver"}

        return {"action": "finish", "params": {"summary": f"Executed actions for '{task}' via CUA Driver."}, "thought": "Completed"}

    def _dispatch_cua_action(self, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Maps an autonomous step directly to the CUA Driver bridge."""
        if action == "launch_app":
            return self.bridge.launch_app(params.get("name", ""))
        elif action == "click":
            return self.bridge.click(x=params.get("x"), y=params.get("y"), button=params.get("button", "LEFT"))
        elif action == "double_click":
            return self.bridge.double_click(x=params.get("x"), y=params.get("y"))
        elif action == "right_click":
            return self.bridge.right_click(x=params.get("x"), y=params.get("y"))
        elif action == "type_text":
            return self.bridge.type_text(params.get("text", ""))
        elif action == "set_value":
            return self.bridge.set_value(params.get("value", ""), element_token=params.get("element_token"))
        elif action == "press_key":
            return self.bridge.press_key(params.get("key", ""), modifiers=params.get("modifiers"))
        elif action == "hotkey":
            return self.bridge.hotkey(params.get("keys", []))
        elif action == "scroll":
            return self.bridge.scroll(
                x=params.get("x", 0),
                y=params.get("y", 0),
                direction=params.get("direction", "DOWN"),
                amount=params.get("amount", 2),
            )
        elif action == "move_cursor":
            return self.bridge.move_cursor(x=params.get("x", 0), y=params.get("y", 0))
        elif action == "get_accessibility_tree":
            return self.bridge.get_accessibility_tree()
        elif action == "browser_navigate":
            return self.bridge.browser_navigate(params.get("url", ""))
        elif action == "verify_state":
            return self.bridge.verify_state(params.get("expect", {}))
        elif action == "zoom":
            return self.bridge.zoom(params.get("x1", 0), params.get("y1", 0), params.get("x2", 0), params.get("y2", 0))
        else:
            # Direct pass-through to CUA driver call_tool
            return self.bridge.call_tool(action, params)


# Singleton instances
_driver_bridge: Optional[CuaDriverBridge] = None
_cua_agent: Optional[AutonomousComputerUseAgent] = None


def get_cua_driver_bridge() -> CuaDriverBridge:
    global _driver_bridge
    if _driver_bridge is None:
        _driver_bridge = CuaDriverBridge.get_instance()
    return _driver_bridge


def get_autonomous_cua_agent() -> AutonomousComputerUseAgent:
    global _cua_agent
    if _cua_agent is None:
        _cua_agent = AutonomousComputerUseAgent(get_cua_driver_bridge())
    return _cua_agent


def execute_computer_task(task: str, max_steps: int = 8, progress_cb: Optional[Callable] = None) -> Dict[str, Any]:
    """Top-level entry point to run an autonomous computer task with CUA Driver."""
    agent = get_autonomous_cua_agent()
    return agent.run_task(task, max_steps=max_steps, progress_cb=progress_cb)
