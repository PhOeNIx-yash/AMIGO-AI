"""
Autonomous Agent Engine for Amigo Assistant.
Provides:
- Turing-complete execution primitives (run_command, run_python, read_file, write_file, edit_file, list_directory, fetch_web_page)
- Autonomous Multi-Step ReAct reasoning loop with dynamic tool discovery (no hardcoded decision trees)
- Self-correction on errors and observation-driven reflection
- Live step-by-step progress callbacks for UI WebSocket streaming and Voice
- Backward-compatible exports: decompose_task, get_desktop_context, execute_desktop_action
"""

import collections
import datetime
import io
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("amigo.task_agent")

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ─────────────────────────────────────────────────────────────────────────────
# 1. UNIVERSAL COMPUTING & AUTOMATION PRIMITIVES ("CAN DO ANYTHING")
# ─────────────────────────────────────────────────────────────────────────────

def execute_command(
    command: str,
    timeout: int = 45,
    cwd: str | None = None,
    shell: str = "powershell",
) -> dict[str, Any]:
    """
    Executes a shell or PowerShell command on the Windows system.
    Returns stdout, stderr, exit code, and execution time.
    """
    if not command or not command.strip():
        return {"success": False, "error": "Empty command provided", "exit_code": -1}

    cmd_str = command.strip()
    working_dir = os.path.abspath(cwd) if cwd and os.path.isdir(cwd) else str(_PROJECT_ROOT)

    t0 = time.perf_counter()
    try:
        # Prefer powershell on Windows for modern scripting, cmd as fallback
        if sys.platform == "win32" and shell.lower() == "powershell":
            full_cmd = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", cmd_str]
        else:
            full_cmd = cmd_str

        proc = subprocess.run(
            full_cmd,
            shell=True if isinstance(full_cmd, str) else False,
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=max(5, min(timeout, 180)),
        )
        duration = round(time.perf_counter() - t0, 3)

        stdout = proc.stdout.strip() if proc.stdout else ""
        stderr = proc.stderr.strip() if proc.stderr else ""

        # Limit output length to prevent context explosion
        if len(stdout) > 4000:
            stdout = stdout[:3800] + f"\n... [truncated, {len(stdout)} total characters]"
        if len(stderr) > 2000:
            stderr = stderr[:1800] + f"\n... [truncated, {len(stderr)} total characters]"

        return {
            "success": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "duration_s": duration,
            "cwd": working_dir,
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": f"Command timed out after {timeout} seconds",
            "exit_code": 124,
            "duration_s": round(time.perf_counter() - t0, 3),
        }
    except Exception as e:
        logger.exception("execute_command failed: %s", e)
        return {
            "success": False,
            "error": str(e),
            "exit_code": -1,
            "duration_s": round(time.perf_counter() - t0, 3),
        }


def execute_python(code: str, timeout: int = 45) -> dict[str, Any]:
    """
    Executes arbitrary Python code in an isolated subprocess.
    Captures stdout, stderr, and output values without crashing the host process.
    """
    if not code or not code.strip():
        return {"success": False, "error": "Empty python code provided"}

    clean_code = code.strip()
    # Strip markdown code block wrapping if model wrapped it
    if clean_code.startswith("```"):
        clean_code = re.sub(r"^```(?:python|py)?\n?", "", clean_code)
        clean_code = re.sub(r"\n?```$", "", clean_code).strip()

    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            [sys.executable, "-c", clean_code],
            capture_output=True,
            text=True,
            timeout=max(5, min(timeout, 120)),
            cwd=str(_PROJECT_ROOT),
        )
        duration = round(time.perf_counter() - t0, 3)
        stdout = proc.stdout.strip() if proc.stdout else ""
        stderr = proc.stderr.strip() if proc.stderr else ""

        if len(stdout) > 4000:
            stdout = stdout[:3800] + f"\n... [truncated, {len(stdout)} chars]"
        if len(stderr) > 2000:
            stderr = stderr[:1800] + f"\n... [truncated, {len(stderr)} chars]"

        return {
            "success": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "duration_s": duration,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Python execution timed out after {timeout}s", "exit_code": 124}
    except Exception as e:
        logger.exception("execute_python failed: %s", e)
        return {"success": False, "error": str(e), "exit_code": -1}


def read_file_content(path: str, start_line: int = 1, end_line: int | None = None) -> dict[str, Any]:
    """Reads lines from a file on disk."""
    try:
        p = Path(path).expanduser().resolve()
        if not p.is_file():
            return {"success": False, "error": f"File does not exist: {path}"}

        with open(p, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        total = len(lines)
        s = max(1, start_line)
        e = min(total, end_line) if end_line else total
        selected = lines[s - 1 : e]
        content = "".join(selected)

        if len(content) > 5000:
            content = content[:4800] + f"\n... [truncated, {len(content)} total characters]"

        return {
            "success": True,
            "path": str(p),
            "start_line": s,
            "end_line": e,
            "total_lines": total,
            "content": content,
        }
    except Exception as e:
        return {"success": False, "error": str(e), "path": path}


def write_file_content(path: str, content: str, append: bool = False) -> dict[str, Any]:
    """Writes or appends content to a file on disk."""
    try:
        p = Path(path).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with open(p, mode, encoding="utf-8") as f:
            f.write(content)
        return {
            "success": True,
            "path": str(p),
            "bytes_written": len(content.encode("utf-8")),
            "mode": "append" if append else "write",
        }
    except Exception as e:
        return {"success": False, "error": str(e), "path": path}


def edit_file_content(path: str, target: str, replacement: str) -> dict[str, Any]:
    """Replaces target string with replacement string in a file."""
    try:
        p = Path(path).expanduser().resolve()
        if not p.is_file():
            return {"success": False, "error": f"File does not exist: {path}"}
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()

        if target not in text:
            return {"success": False, "error": "Target string not found in file", "path": str(p)}

        count = text.count(target)
        new_text = text.replace(target, replacement)
        with open(p, "w", encoding="utf-8") as f:
            f.write(new_text)

        return {"success": True, "path": str(p), "replacements_made": count}
    except Exception as e:
        return {"success": False, "error": str(e), "path": path}


def list_dir_contents(path: str = ".", max_items: int = 60) -> dict[str, Any]:
    """Lists files and directories in a path with metadata."""
    try:
        p = Path(path).expanduser().resolve()
        if not p.is_dir():
            return {"success": False, "error": f"Directory does not exist: {path}"}

        items = []
        for entry in p.iterdir():
            try:
                is_dir = entry.is_dir()
                size = 0 if is_dir else entry.stat().st_size
                items.append({
                    "name": entry.name,
                    "is_dir": is_dir,
                    "size": size,
                    "ext": entry.suffix.lower() if not is_dir else "",
                })
            except Exception:
                continue

        # Sort: directories first, then alphabetically
        items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        truncated = len(items) > max_items
        shown = items[:max_items]

        return {
            "success": True,
            "path": str(p),
            "total_items": len(items),
            "items": shown,
            "truncated": truncated,
        }
    except Exception as e:
        return {"success": False, "error": str(e), "path": path}


def fetch_web_page(url: str, timeout: int = 15) -> dict[str, Any]:
    """Fetches HTML or JSON content from a web URL."""
    try:
        parsed = urllib.parse.urlparse(url)
        if not parsed.scheme:
            url = "https://" + url

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/json,text/plain;q=0.9,*/*;q=0.8",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status = response.status
            raw = response.read(300000)
            encoding = response.headers.get_content_charset() or "utf-8"
            body = raw.decode(encoding, errors="replace")

        # Strip scripts, styles, and extract text if HTML
        if "<html" in body.lower():
            body = re.sub(r"<(?:script|style)[^>]*>[\s\S]*?</(?:script|style)>", "", body, flags=re.I)
            body = re.sub(r"<[^>]+>", " ", body)
            body = re.sub(r"\s+", " ", body).strip()

        if len(body) > 4000:
            body = body[:3800] + f"\n... [truncated, {len(body)} chars]"

        return {"success": True, "url": url, "status_code": status, "content": body}
    except Exception as e:
        return {"success": False, "error": str(e), "url": url}


# ─────────────────────────────────────────────────────────────────────────────
# 2. AUTONOMOUS AGENT REASONING ENGINE (REACT MULTI-STEP LOOP)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class StepRecord:
    step: int
    thought: str
    tool: str
    params: dict[str, Any]
    observation: Any
    duration_s: float = 0.0
    error: str | None = None


@dataclass
class AgentResult:
    goal: str
    success: bool
    final_answer: str
    steps: list[StepRecord] = field(default_factory=list)
    total_duration_s: float = 0.0
    error: str | None = None


def _clean_agent_response(text: str) -> str:
    """Cleans raw agent text, unescaping newlines and stripping internal JSON or XML artifacts."""
    if not text or not isinstance(text, str):
        return ""
    clean = text.strip()
    # Unescape escaped newlines if model output literal \n in string
    if "\\n" in clean and "\n" not in clean:
        clean = clean.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')

    # Strip any leading/trailing JSON wrapper blocks if leaked
    clean = re.sub(r'<(?:tool_call|invoke|function)[^>]*>[\s\S]*?</(?:tool_call|invoke|function)>', '', clean, flags=re.I)
    clean = re.sub(r'^\s*\{\s*"thought"[\s\S]*?"(?:final_answer|response|reply)"\s*:\s*"?', '', clean, flags=re.I)
    clean = re.sub(r'"?\s*,\s*"is_complete"\s*:\s*(?:true|false)[\s\S]*$', '', clean, flags=re.I)
    clean = re.sub(r'"?\s*\}\s*$', '', clean)

    # Strip Final Answer: prefix if present
    clean = re.sub(r'^(?:Final Answer|Final Summary|Answer)\s*:\s*', '', clean, flags=re.I)
    return clean.strip()


class AutonomousAgent:
    """
    Goal-driven autonomous agent that plans, chooses tools dynamically, executes actions,
    observes environment feedback, self-corrects, and iterates until the goal is achieved.
    No hardcoded branch rules: all decisions are driven by dynamic reasoning and tool schemas.
    """

    def __init__(self, max_steps: int = 8, timeout_seconds: float = 120.0):
        self.max_steps = max_steps
        self.timeout_seconds = timeout_seconds

    def _get_dynamic_tool_manifest(self) -> str:
        """Retrieves active tool descriptions from tool_registry dynamically."""
        from amigo.utils.tool_registry import TOOL_DEFINITIONS
        lines = []
        for t in TOOL_DEFINITIONS:
            name = t.get("name", "")
            desc = t.get("description", "")
            params = (t.get("parameters") or {}).get("properties") or {}
            req = set((t.get("parameters") or {}).get("required") or [])
            sig = ", ".join(f"{k}*" if k in req else k for k in params)
            lines.append(f"- {name}({sig}): {desc}")
        return "\n".join(lines)

    def _build_step_prompt(
        self,
        goal: str,
        steps: list[StepRecord],
        context: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        now_str = datetime.datetime.now().strftime("%A, %B %d, %Y %I:%M %p")
        tools_str = self._get_dynamic_tool_manifest()

        system_prompt = (
            "You are Amigo, an autonomous personal AI agent capable of executing complex tasks on this Windows computer.\n"
            "You accomplish high-level user goals by breaking them down into steps, selecting appropriate tools, "
            "inspecting tool observations, self-correcting if an action fails, and completing the goal autonomously.\n\n"
            "OPERATING GUIDELINES:\n"
            "- Work step-by-step. In each turn, provide a clear 'thought' explaining your reasoning and choose one 'action' (tool + parameters).\n"
            "- When an action returns an observation, analyze whether you need more information or another action.\n"
            "- If an action fails or returns an error, reflect on the error in your thought and try a different approach.\n"
            "- When you have accomplished the goal completely or answered the user's question, write 'Final Answer:' with your direct, clear response.\n\n"
            "AVAILABLE DYNAMIC TOOLS:\n"
            f"{tools_str}\n\n"
            "TOOL CALLING FORMAT:\n"
            "To call a tool, use XML tool format:\n"
            "<tool_call>\n"
            "<function=tool_name>\n"
            "<parameter=param_name>param_value</parameter>\n"
            "</function>\n"
            "</tool_call>\n"
            "OR JSON format: {\"tool\": \"tool_name\", \"params\": {\"param_name\": \"param_value\"}}\n\n"
            "When done, conclude with:\n"
            "Final Answer: <your response to the user>"
        )

        history_lines = [f"Goal: {goal}\n[Current System Time: {now_str}]"]
        if context:
            ctx_summary = ", ".join(f"{k}: {v}" for k, v in context.items() if v and isinstance(v, (str, int, float, bool)))
            if ctx_summary:
                history_lines.append(f"Context: {ctx_summary}")

        if steps:
            history_lines.append("\n--- Execution History ---")
            for s in steps:
                history_lines.append(f"Step {s.step}:")
                history_lines.append(f"  Thought: {s.thought}")
                history_lines.append(f"  Action: {s.tool}({json.dumps(s.params)})")
                obs_str = str(s.observation)
                if len(obs_str) > 600:
                    obs_str = obs_str[:550] + " ... [truncated]"
                history_lines.append(f"  Observation: {obs_str}")

        history_lines.append("\nPlan the next step or conclude if done:")
        user_prompt = "\n".join(history_lines)

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    def _parse_agent_step(self, response_text: str) -> tuple[str, str | None, dict[str, Any], bool, str]:
        """Parses model response into (thought, tool_name, params, is_complete, final_answer)."""
        from amigo.core.llm_agent import _strip_reasoning
        clean, thought = _strip_reasoning(response_text)
        clean = re.sub(r"```(?:json|xml)?\s*", "", clean).replace("```", "").strip()

        # 1. First priority: Check for XML / LiquidAI tool calling
        fn_m = re.search(r'<function=([a-zA-Z0-9_]+)>([\s\S]*?)</function>', clean, re.I)
        if not fn_m:
            fn_m = re.search(r'<invoke\s+name=["\']([^"\']+)["\']\s*>([\s\S]*?)</invoke>', clean, re.I)

        if fn_m:
            t_name = fn_m.group(1).strip().lower()
            body = fn_m.group(2)
            params = {}
            for p in re.finditer(r'<(?:parameter=([a-zA-Z0-9_]+)|parameter\s+name=["\']([^"\']+)["\'])\s*>\s*([\s\S]*?)\s*</parameter>', body, re.I):
                k = (p.group(1) or p.group(2)).strip()
                v = p.group(3).strip()
                params[k] = v
            th = clean[:fn_m.start()].strip() or thought or "Executing tool action."
            th = re.sub(r'^(?:Thought|Plan)\s*:\s*', '', th, flags=re.I)
            return th, t_name, params, False, ""

        # 2. Second priority: Check for JSON tool calls anywhere in the output
        m = re.search(r'\{\s*"?(?:tool|action)"?[\s\S]*?\}', clean)
        if not m:
            m = re.search(r'\{[\s\S]*"?(?:tool|action)"?[\s\S]*\}', clean)

        if m:
            data = None
            try:
                data = json.loads(m.group(0))
            except Exception:
                t_m = re.search(r'"(?:tool|action|name)"\s*:\s*"([a-zA-Z0-9_]+)"', clean)
                if t_m:
                    t_name = t_m.group(1).lower()
                    params = {}
                    for pm in re.finditer(r'"([a-zA-Z0-9_]+)"\s*:\s*"((?:[^"\\]|\\.)*)"', clean):
                        pk = pm.group(1)
                        if pk not in ("tool", "action", "name", "thought", "is_complete", "final_answer"):
                            params[pk] = pm.group(2)
                    data = {"tool": t_name, "params": params}

            if data and isinstance(data, dict):
                tool_name = data.get("tool") or (data.get("action") if isinstance(data.get("action"), str) else None)
                if isinstance(data.get("action"), dict):
                    tool_name = data["action"].get("tool") or data["action"].get("name")
                    params = data["action"].get("params") or {}
                else:
                    params = data.get("params") or {}

                if tool_name and str(tool_name).strip().lower() != "chat":
                    th = clean[:m.start()].strip() or data.get("thought", "") or thought or "Executing tool action."
                    th = re.sub(r'^(?:Thought|Plan)\s*:\s*', '', th, flags=re.I)
                    return th, str(tool_name).strip().lower(), params, False, ""

        # 3. Third priority: If NO tool was called, check for Final Answer indicator
        fa_m = re.search(r'(?:Final Answer|Final Summary)\s*:\s*([\s\S]+)$', clean, re.I)
        if fa_m:
            ans = _clean_agent_response(fa_m.group(1))
            th = clean[:fa_m.start()].strip() or thought or "Goal complete."
            return th, None, {}, True, ans

        # 4. Fallback: clean non-JSON answer
        return thought or "Completed.", None, {}, True, _clean_agent_response(clean)

    def run(
        self,
        goal: str,
        context: dict[str, Any] | None = None,
        on_step: Callable[[StepRecord], None] | None = None,
    ) -> AgentResult:
        """
        Executes the autonomous loop for the given goal.
        Streams step updates via `on_step` callback if provided.
        """
        from amigo.core.llm_agent import query_local_llm
        from amigo.utils.tool_registry import execute_tool

        t_start = time.perf_counter()
        steps: list[StepRecord] = []
        clean_goal = (goal or "").strip()

        if not clean_goal:
            return AgentResult(goal="", success=False, final_answer="No goal provided.", total_duration_s=0.0)

        for step_idx in range(1, self.max_steps + 1):
            if time.perf_counter() - t_start > self.timeout_seconds:
                logger.warning("AutonomousAgent timed out after %.1fs", self.timeout_seconds)
                break

            messages = self._build_step_prompt(clean_goal, steps, context)
            t_step_start = time.perf_counter()
            try:
                response = query_local_llm(
                    messages,
                    system_prompt="",
                    max_tokens=600,
                    temperature=0.1,
                    thinking=False,
                    sanitize=False,
                    stop=["<|im_end|>", "<|endoftext|>", "<eos>"],
                )
            except Exception as e:
                logger.exception("Agent LLM query failed: %s", e)
                return AgentResult(
                    goal=clean_goal,
                    success=False,
                    final_answer="I encountered an internal issue reasoning about that task.",
                    steps=steps,
                    total_duration_s=round(time.perf_counter() - t_start, 2),
                    error=str(e),
                )

            thought, tool_name, params, is_complete, final_answer = self._parse_agent_step(response)

            if not tool_name or tool_name == "chat":
                answer = final_answer or (params.get("response") if isinstance(params, dict) else "") or thought
                answer = _clean_agent_response(answer)
                rec = StepRecord(
                    step=step_idx,
                    thought=thought,
                    tool=tool_name or "chat",
                    params=params,
                    observation=answer,
                    duration_s=round(time.perf_counter() - t_step_start, 2),
                )
                steps.append(rec)
                if on_step:
                    on_step(rec)
                return AgentResult(
                    goal=clean_goal,
                    success=True,
                    final_answer=answer,
                    steps=steps,
                    total_duration_s=round(time.perf_counter() - t_start, 2),
                )

            # Execute the action via the dynamic tool registry
            t_exec = time.perf_counter()
            spoken, url, meta = execute_tool(tool_name, params, query=clean_goal)
            duration_exec = round(time.perf_counter() - t_exec, 3)

            obs = meta if meta else spoken
            err = meta.get("error") if isinstance(meta, dict) else None

            rec = StepRecord(
                step=step_idx,
                thought=thought,
                tool=tool_name,
                params=params,
                observation=obs,
                duration_s=duration_exec,
                error=str(err) if err else None,
            )
            steps.append(rec)
            if on_step:
                on_step(rec)

            if is_complete and final_answer:
                return AgentResult(
                    goal=clean_goal,
                    success=True,
                    final_answer=_clean_agent_response(final_answer),
                    steps=steps,
                    total_duration_s=round(time.perf_counter() - t_start, 2),
                )

        # Loop finished without explicit complete: generate final conclusion
        from amigo.core.llm_agent import query_local_llm
        summary_prompt = [
            {"role": "system", "content": "You are Amigo. Provide a concise, clear final summary of what you did and the results achieved."},
            {"role": "user", "content": f"Goal: {clean_goal}\nActions Taken: {len(steps)} steps.\nLast Observation: {steps[-1].observation if steps else 'None'}"},
        ]
        try:
            final = query_local_llm(summary_prompt, max_tokens=256, temperature=0.2)
        except Exception:
            final = f"Completed {len(steps)} steps towards your goal."

        return AgentResult(
            goal=clean_goal,
            success=True,
            final_answer=final,
            steps=steps,
            total_duration_s=round(time.perf_counter() - t_start, 2),
        )


def run_autonomous_agent(
    goal: str,
    context: dict[str, Any] | None = None,
    on_step: Callable[[StepRecord], None] | None = None,
    max_steps: int = 8,
) -> AgentResult:
    """Convenience helper to launch the autonomous agent loop."""
    agent = AutonomousAgent(max_steps=max_steps)
    return agent.run(goal, context=context, on_step=on_step)


# ─────────────────────────────────────────────────────────────────────────────
# 3. BACKWARD-COMPATIBLE UTILITIES & TASK DECOMPOSITION
# ─────────────────────────────────────────────────────────────────────────────

_PRESERVED_PHRASES = (
    "bed and breakfast",
    "rock and roll",
    "mac and cheese",
    "pros and cons",
    "peace and quiet",
    "law and order",
    "trial and error",
    "wait and see",
    "search for",
    "look up",
)


def decompose_task(query: str) -> list[str]:
    """
    Decomposes compound user queries (e.g. 'close Chrome and search the web')
    into distinct executable steps while preserving idiomatic natural phrases
    such as 'bed and breakfast' or 'rock and roll'.
    """
    if not query or not query.strip():
        return []

    q = query.strip()
    q_low = q.lower()

    # If the query contains an idiomatic preserved phrase, do not split on 'and'
    for phrase in _PRESERVED_PHRASES:
        if phrase in q_low:
            return [q]

    # Split on conjunctions between distinct imperative action clauses
    # Matches ' and ', ' then ', ' also ', ' followed by ', ' afterward '
    parts = re.split(r"\b(?:and\s+then|then|afterward|after\s+that|and)\b", q, flags=re.IGNORECASE)
    cleaned = [p.strip() for p in parts if p.strip()]

    # Filter out spurious single-word splits unless they are commands
    if len(cleaned) <= 1:
        return [q]

    return cleaned


def get_desktop_context() -> dict[str, Any]:
    """Returns active desktop telemetry and environment state for the agent."""
    from amigo.core.llm_agent import get_last_screen_text
    from amigo.services.os_automation import get_system_status

    ctx: dict[str, Any] = {
        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "platform": sys.platform,
    }
    try:
        ctx["system"] = get_system_status()
    except Exception:
        pass

    try:
        if screen := get_last_screen_text():
            ctx["screen_ocr"] = screen[:500]
    except Exception:
        pass

    return ctx


def execute_desktop_action(action: str, params: dict[str, Any] | None = None) -> Any:
    """Executes a desktop action through the central tool registry."""
    from amigo.utils.tool_registry import execute_tool
    spoken, url, meta = execute_tool(action, params or {})
    return meta if meta else spoken

