"""Unified evaluation, stress, and latency runner for Amigo (isolated from rag_data/)."""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from tests import mock_os_side_effects
from tests.held_out_eval_set import DEV_EVAL_DATA, _RAW_EVAL_DATA
from tests.test_amigo_capabilities import TEST_QUERIES, map_expected_tool

BASELINE_DEV_PASSED_IDS = {
    0, 2, 4, 6, 8, 10, 12, 16, 18, 20, 22, 24, 26, 30, 32, 34, 36, 40, 42, 44,
    48, 50, 52, 54, 56, 58, 64, 68, 70, 72, 74, 76, 78, 80, 82, 84, 86, 88, 90,
    92, 94, 96, 98, 100, 102, 104, 106, 108, 110, 112, 114, 116, 118, 120, 122,
    124, 126, 128, 130, 132, 134, 136, 138, 144, 148, 150, 152, 154, 156, 158,
    160, 162, 164, 166, 168, 170, 172, 174, 176, 178, 182, 184, 186, 188, 190,
    192, 194, 196, 198,
}

STRESS_QUERIES = [
    ("open_app", "open it"), ("open_app", "open that"), ("open_app", "launch the app"), ("open_app", "open please"),
    ("open_app", "start app"), ("open_app", "open chrome browser"), ("open_app", "launch application"), ("open_app", "open the program"),
    ("set_volume", "set the volume"), ("set_volume", "change volume"), ("set_volume", "volume"), ("set_volume", "set volume to 45 please"),
    ("set_volume", "adjust volume"), ("set_volume", "set volume level"), ("set_volume", "turn volume up"), ("set_volume", "mute the sound"),
    ("open_settings", "open settings"), ("open_settings", "settings please"), ("open_settings", "turn off wifi"), ("open_settings", "toggle wifi"),
    ("open_settings", "open windows settings tab"), ("open_settings", "switch off setting"), ("open_settings", "change a setting"), ("open_settings", "app settings"),
    ("change_setting", "toggle bluetooth"), ("change_setting", "turn night light on"), ("change_setting", "switch dark mode"), ("change_setting", "set the setting"),
    ("change_setting", "enable setting"), ("change_setting", "adjust setting"), ("change_setting", "toggle wi-fi"), ("change_setting", "change system setting"),
    ("get_setting", "check bluetooth"), ("get_setting", "is wifi enabled"), ("get_setting", "what is night light set to"), ("get_setting", "check dark mode status"),
    ("get_setting", "setting status"), ("get_setting", "read setting"), ("get_setting", "is airplane mode on"), ("get_setting", "current setting state"),
    ("read_emails", "check my emails"), ("read_emails", "unread messages"), ("read_emails", "any unread mail"), ("read_emails", "search emails for invoice"),
    ("read_emails", "latest email from Alex"), ("read_emails", "show inbox unread"), ("read_emails", "read my mail"), ("read_emails", "search mail about budget"),
    ("open_file", "open the file"), ("open_file", "open doc"), ("open_file", "launch document"), ("open_file", "open first file"),
    ("open_file", "open second result"), ("open_file", "open pdf"), ("open_file", "open spreadsheet"), ("open_file", "open file please"),
    ("open_website", "go to python.org"), ("open_website", "google search query"), ("open_website", "open website"), ("open_website", "browse to github"),
    ("open_website", "open https://news.ycombinator.com"), ("open_website", "visit reddit.com"), ("open_website", "open web page"), ("open_website", "launch site"),
    ("document_qa", "what does the doc say"), ("document_qa", "read the document"), ("document_qa", "summarize the invoice"), ("document_qa", "check notes in the file"),
    ("document_qa", "what is in the pdf"), ("document_qa", "explain the second document"), ("document_qa", "read file content"), ("document_qa", "look inside document"),
]


@contextmanager
def isolated_rag_data():
    """Run evals against a temporary copy of rag_data so live memory files are never touched."""
    from amigo.core import rag_engine

    tmpdir = tempfile.mkdtemp(prefix="amigo_eval_rag_")
    tmp_mv2 = os.path.join(tmpdir, "amigo_memory.mv2")
    tmp_prof = os.path.join(tmpdir, "amigo_profile.json")
    live_mv2 = rag_engine.MEMVID_PATH
    if os.path.exists(live_mv2):
        try:
            shutil.copy2(live_mv2, tmp_mv2)
        except OSError:
            pass
    if os.path.exists(rag_engine.PROFILE_FILE):
        try:
            shutil.copy2(rag_engine.PROFILE_FILE, tmp_prof)
        except OSError:
            pass

    orig = (rag_engine.RAG_DATA_DIR, rag_engine.MEMVID_PATH, rag_engine.PROFILE_FILE, rag_engine._mv_instance)
    rag_engine.RAG_DATA_DIR = tmpdir
    rag_engine.MEMVID_PATH = tmp_mv2
    rag_engine.PROFILE_FILE = tmp_prof
    rag_engine._mv_instance = None
    try:
        yield tmpdir
    finally:
        rag_engine._mv_instance = orig[3]
        rag_engine.PROFILE_FILE = orig[2]
        rag_engine.MEMVID_PATH = orig[1]
        rag_engine.RAG_DATA_DIR = orig[0]
        rag_engine.clear_search_cache()
        shutil.rmtree(tmpdir, ignore_errors=True)


def measure_prompt_tokens(llm) -> int:
    from amigo.utils.tool_registry import get_static_master_prompt

    prompt = get_static_master_prompt()
    tok_exe = os.path.join(ROOT_DIR, "amigo", "lib", "llama", "llama-tokenize.exe")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as tf:
        tf.write(prompt)
        tmp_path = tf.name
    try:
        res = subprocess.run([tok_exe, "-m", llm.model_path, "-f", tmp_path, "--ids"], capture_output=True, text=True)
        m = re.search(r"(\[[\d,\s]+\])\s*$", (res.stdout or "").strip())
        return len([x for x in m.group(1).strip("[]").split(",") if x.strip()]) if m else 0
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def run_gate() -> int:
    from amigo.core import llm_agent

    llm = llm_agent.init_local_llm()
    _ = llm_agent.get_agent_action("what time is it")
    tok_cnt = measure_prompt_tokens(llm)
    print(f"Static Prompt Tokens (llama-tokenize): {tok_cnt} (limit <= 3500)", flush=True)

    orig_ok = 0
    for q, raw_exp in TEST_QUERIES:
        exp = map_expected_tool(raw_exp)
        acts = llm_agent.get_agent_action(q)
        got = acts[0]["tool"] if acts else "none"
        if got == exp:
            orig_ok += 1
        else:
            print(f"  [ORIG MISS] {q!r} -> {got} (expected {exp})", flush=True)
    print(f"TEST_QUERIES (33 mapped): {orig_ok}/{len(TEST_QUERIES)} ({orig_ok*100/len(TEST_QUERIES):.1f}%)", flush=True)

    dev_ok = 0
    regressions = []
    misses_by_cause = defaultdict(list)
    for item in DEV_EVAL_DATA:
        idx = _RAW_EVAL_DATA.index(item)
        q, raw_exp, cat, hist = item["query"], item["expected_tool"], item["category"], item.get("history")
        exp = map_expected_tool(raw_exp)
        acts = llm_agent.get_agent_action(q, conversation_history=hist)
        got = acts[0]["tool"] if acts else "none"
        if got == exp:
            dev_ok += 1
        else:
            misses_by_cause[f"{exp} -> {got}"].append((idx, cat, q))
            if idx in BASELINE_DEV_PASSED_IDS:
                regressions.append((idx, cat, q, exp, got))

    print(f"DEV_EVAL_DATA (100 mapped): {dev_ok}/{len(DEV_EVAL_DATA)} ({dev_ok*100/len(DEV_EVAL_DATA):.1f}%)", flush=True)
    print(f"DEV Regressions vs 89/100 baseline: {len(regressions)}", flush=True)
    for r in regressions:
        print(f"  REGRESSION [id={r[0]}, {r[1]}]: {r[2]!r} -> {r[4]} (expected {r[3]})", flush=True)
    if misses_by_cause:
        print("Grouped misses:", {k: [x[0] for x in v] for k, v in sorted(misses_by_cause.items(), key=lambda kv: -len(kv[1]))}, flush=True)

    if tok_cnt > 3500 or regressions or dev_ok < len(BASELINE_DEV_PASSED_IDS):
        return 1
    return 0


class _ShadowHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.hits: list[str] = []

    def emit(self, record):
        msg = self.format(record)
        if "[SHADOW-FALLBACK]" in msg:
            self.hits.append(msg)


def run_stress() -> int:
    from amigo.core import llm_agent, rag_engine
    from amigo.services import os_automation as osa
    from amigo.utils import tool_registry as tr

    _ = llm_agent.get_agent_action("what time is it")
    sh = _ShadowHandler()
    tr.logger.addHandler(sh)
    fallback_counts: Counter[str] = Counter()
    clarify_cnt = 0

    with mock_os_side_effects(), ExitStack() as stack:
        for fn in ("open_windows_app", "is_app_already_running", "open_setting"):
            if hasattr(tr, fn):
                stack.enter_context(patch.object(tr, fn, return_value="Opened Windows Settings (mocked)." if fn == "open_setting" else (fn != "is_app_already_running")))
        stack.enter_context(patch("amigo.utils.tool_registry.webbrowser.open", return_value=True))
        for fn in ("type_text", "press_shortcut"):
            if hasattr(osa, fn):
                stack.enter_context(patch.object(osa, fn, return_value=True))
        try:
            import amigo.services.screen_vision as sv
            if hasattr(sv, "inspect_screen"):
                stack.enter_context(patch.object(sv, "inspect_screen", return_value={"reply": "Mocked screen.", "screen_text": ""}))
        except Exception:
            pass

        for i, (cat, q) in enumerate(STRESS_QUERIES, 1):
            tr._PENDING_CONFIRMATION = None
            for slot in ("active_file", "active_app", "last_search", "active_subject", "current_media", "found_files", "recent_items", "last_link"):
                rag_engine.update_active_state(slot, None)
            sh.hits.clear()
            acts, rresp = tr.get_agent_actions(q)
            res = tr.execute_actions(acts, query=q)
            spoken = res[0][0] if res else rresp
            t_name = acts[0]["tool"] if acts else "none"
            p_dict = acts[0].get("params", {}) if acts else {}
            for h in sh.hits:
                key = h.split("[SHADOW-FALLBACK]", 1)[1].split("fired", 1)[0].strip()
                fallback_counts[key] += 1
            if "?" in (spoken or "") or t_name == "clarification":
                clarify_cnt += 1
            print(f"  [{i:2d}|{cat:14s}] {q!r:34s} -> {t_name}({p_dict}) | hits={len(sh.hits)} | spoken={spoken!r}", flush=True)

    tr.logger.removeHandler(sh)
    print(f"STRESS SUMMARY: total={len(STRESS_QUERIES)}, spoken_questions={clarify_cnt}, total_shadow_hits={sum(fallback_counts.values())}, per_fallback={dict(fallback_counts)}", flush=True)
    return 0


def run_latency() -> int:
    from amigo.core import llm_agent

    _ = llm_agent.get_agent_action("what time is it")
    sample = [q for q, _ in TEST_QUERIES[:10]]
    all_runs = []
    for q in sample:
        for _ in range(10):
            t0 = time.perf_counter()
            _ = llm_agent.get_agent_action(q)
            all_runs.append((time.perf_counter() - t0) * 1000)

    all_sorted = sorted(all_runs)
    med = statistics.median(all_sorted)
    p95 = all_sorted[int(len(all_sorted) * 0.95)]
    print(f"LATENCY ({len(all_runs)} runs across {len(sample)} queries): median={med:.1f} ms | p95={p95:.1f} ms", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Amigo evaluation suite (gate, stress, latency).")
    parser.add_argument("mode", choices=["gate", "stress", "latency"])
    args = parser.parse_args()
    with isolated_rag_data():
        if args.mode == "gate":
            return run_gate()
        if args.mode == "stress":
            return run_stress()
        return run_latency()


if __name__ == "__main__":
    raise SystemExit(main())
