"""Unit tests for Rust-native Roamies features integrated into Amigo."""

from __future__ import annotations

import json
from unittest.mock import patch

from amigo.services import hands, screen_pen


def test_hands_rust_native_bounds_and_pointing_math():
    with patch("amigo.services.hands.is_available", return_value=True), \
         patch("amigo.services.hands.windows", return_value={"ok": True, "windows": [{"bounds": [0, 0, 2560, 1440]}]}):
        assert hands.get_screen_bounds() == (2560.0, 1440.0)

    m = hands.shot_map([100, 200, 1000, 500], 2000, 1000)
    assert hands.shot_fits([100, 200, 1000, 500], 2000, 1000) is True
    assert hands.shot_fits([100, 200, 1000, 500], 1000, 1000) is False
    assert hands.to_screen(m, 400, 200) == (300, 300)

    snapped = hands.snap_rect(
        [102, 198, 96, 40],
        [{"bounds": [100, 200, 100, 40]}, {"bounds": [500, 500, 100, 40]}],
    )
    assert snapped == [100.0, 200.0, 100.0, 40.0]


def test_screen_pen_diagram_variants_and_guide():
    quiz_payload = json.dumps({
        "summary": "Quick Python quiz.",
        "visual_type": "quiz",
        "questions": [{
            "text": "What is len([1, 2])?",
            "options": ["1", "2", "3"],
            "right": 1,
            "explain": "Two items.",
        }],
    })
    with patch("amigo.core.llm_agent.query_local_llm", return_value=quiz_payload), \
         patch("amigo.core.llm_agent.sanitize_for_tts", side_effect=lambda s: s):
        beats, summary = screen_pen._generate_visual_diagram_beats("python")
        assert summary == "Quick Python quiz."
        kinds = [mk["kind"] for mk in beats[0]["marks"]]
        assert "quiz" in kinds

    model_payload = json.dumps({
        "summary": "Atom 3D model.",
        "visual_type": "model3d",
        "parts": [{"shape": "sphere", "label": "Nucleus", "tone": "coral"}],
    })
    with patch("amigo.core.llm_agent.query_local_llm", return_value=model_payload), \
         patch("amigo.core.llm_agent.sanitize_for_tts", side_effect=lambda s: s):
        beats, _ = screen_pen._generate_visual_diagram_beats("atom")
        kinds = [mk["kind"] for mk in beats[0]["marks"]]
        assert "model" in kinds

    with patch("amigo.services.screen_pen._resolve_target_rect", return_value=[10.0, 20.0, 80.0, 30.0]):
        res = screen_pen.screen_pen_guide([
            {"target": "File", "caption": "Open File menu"},
            {"target": "Export", "caption": "Click Export"},
        ])
        assert res["status"] == "success"
        assert res["step_count"] == 2
        assert res["resolved_targets"] == 2
        assert res["event"]["beats"][0]["marks"][0]["kind"] == "ring"


def test_resolve_target_rect_falls_back_to_screen():
    def fake_find_text(tgt, text, scroll=False):
        if tgt == "screen" and text == "Research":
            return {"ok": True, "matches": [{"bounds": [120, 45, 90, 28]}]}
        return {"ok": True, "matches": []}

    with patch("amigo.services.hands.find_text", side_effect=fake_find_text), \
         patch("amigo.services.hands.ocr_find", return_value={"ok": True, "matches": []}):
        rect = screen_pen._resolve_target_rect("Research")
        assert rect == [120.0, 45.0, 90.0, 28.0]


def test_act_in_app_and_read_window_tool_execution():
    from amigo.utils.tool_registry import UI_TOOL_HANDLERS, _validate_action_plan

    # Test act_in_app handler exists and accepts steps
    act_handler = UI_TOOL_HANDLERS["act_in_app"]
    with patch("amigo.services.hands.act", return_value={"ok": True, "done": ["clicked Save", "typed notes.txt"]}) as mock_act:
        reply, _, meta = act_handler({
            "app": "Notepad",
            "steps": [
                {"do": "click_text", "text": "Save"},
                {"do": "fill", "field": "File name", "text": "notes.txt"},
            ],
        }, "", "")
        assert "clicked Save" in reply
        assert meta["hands_used"] is True
        mock_act.assert_called_once()
        called_plan = mock_act.call_args[0][0]
        assert called_plan["app"] == "Notepad"
        assert len(called_plan["steps"]) == 2

    # Test read_window handler
    read_handler = UI_TOOL_HANDLERS["read_window"]
    fake_ui = {
        "ok": True,
        "window": {"app": "Notepad", "title": "Untitled - Notepad"},
        "controls": [
            {"id": 1, "role": "button", "label": "File", "offscreen": False},
            {"id": 2, "role": "edit", "label": "Text Editor", "value": "Hello world", "offscreen": False},
        ],
        "texts": ["File", "Edit", "View", "Hello world"],
    }
    with patch("amigo.services.hands.is_available", return_value=True), \
         patch("amigo.services.hands.ui", return_value=fake_ui):
        summary, _, res = read_handler({"target": "Notepad"}, "", "")
        assert "Notepad — Untitled - Notepad" in summary
        assert "[1] button \"File\"" in summary
        assert "[2] edit \"Text Editor\"" in summary

    # Test validation allows act_in_app with steps and shorthand
    errs1 = _validate_action_plan([{"tool": "act_in_app", "params": {"steps": [{"do": "click_text", "text": "OK"}]}}], "click OK")
    assert not errs1

    errs2 = _validate_action_plan([{"tool": "act_in_app", "params": {"action": "click", "target": "Submit"}}], "click Submit")
    assert not errs2


def test_screen_pen_structured_data_and_roamie_host_envelope():
    from amigo.services import screen_pen

    # 1. Test dual-compatible Roamies host envelope
    inner = {"type": "ink", "id": "test-1", "beats": []}
    envelope = screen_pen._wrap_roamie_host_event(inner)
    assert envelope["kind"] == "agent"
    assert envelope["e"]["type"] == "draw"
    assert envelope["e"]["id"] == "test-1"

    clear_inner = {"type": "ink_clear", "all": True}
    clear_env = screen_pen._wrap_roamie_host_event(clear_inner)
    assert clear_env["kind"] == "agent"
    assert clear_env["e"]["type"] == "clear"

    # 2. Test direct structured diagram payload bypasses LLM
    with patch("amigo.core.llm_agent.query_local_llm") as mock_llm:
        res = screen_pen.screen_pen_annotate(
            action="flow",
            topic="Auth Workflow",
            data={
                "nodes": [
                    {"id": "login", "text": "Login Screen", "shape": "box", "tone": "teal"},
                    {"id": "verify", "text": "2FA Check", "shape": "diamond", "tone": "amber"},
                ],
                "links": [{"from": "login", "to": "verify", "flow": True}],
            },
        )
        assert res["status"] == "success"
        mock_llm.assert_not_called()
        mark = res["event"]["beats"][0]["marks"][0]
        assert mark["kind"] == "flow"
        assert len(mark["nodes"]) == 2
        assert "lay" in mark


def test_multilingual_and_zero_hardcoding():
    from amigo.services import screen_pen
    from amigo.utils.tool_registry import _tool_screen_pen_annotate

    # 1. Test Unicode-aware resolution handles Hindi and Japanese labels natively
    fake_hindi_ui = {
        "ok": True,
        "controls": [
            {"id": 10, "role": "button", "label": "सेटिंग्स", "bounds": [150.0, 80.0, 90.0, 35.0]},
            {"id": 11, "role": "button", "label": "इतिहास", "bounds": [300.0, 80.0, 80.0, 35.0]},
        ],
    }
    with patch("amigo.services.hands.ui", return_value=fake_hindi_ui):
        rect_hindi = screen_pen._resolve_target_rect("सेटिंग्स")
        assert rect_hindi == [150.0, 80.0, 90.0, 35.0]

        rect_hindi_hist = screen_pen._resolve_target_rect("इतिहास")
        assert rect_hindi_hist == [300.0, 80.0, 80.0, 35.0]

    fake_japanese_ui = {
        "ok": True,
        "controls": [
            {"id": 20, "role": "button", "label": "保存", "bounds": [400.0, 120.0, 70.0, 30.0]},
            {"id": 21, "role": "edit", "label": "検索", "bounds": [50.0, 120.0, 200.0, 30.0]},
        ],
    }
    with patch("amigo.services.hands.ui", return_value=fake_japanese_ui):
        rect_ja_save = screen_pen._resolve_target_rect("保存")
        assert rect_ja_save == [400.0, 120.0, 70.0, 30.0]

    # 2. Test _tool_screen_pen_annotate passes actions directly without keyword matching
    with patch("amigo.services.screen_pen.screen_pen_annotate", return_value={"status": "success", "drawn": "map", "summary": "Map displayed"}) as mock_annotate:
        msg, _, res = _tool_screen_pen_annotate({"action": "map", "topic": "Japan, Germany, Brazil"}, "natively routed query", False)
        assert msg == "Map displayed"
        mock_annotate.assert_called_once()
        assert mock_annotate.call_args[1]["action"] == "map"
        assert mock_annotate.call_args[1]["topic"] == "Japan, Germany, Brazil"
        assert mock_annotate.call_args[1]["label"] is None


    # 3. Target not found returns explicit error
    with patch("amigo.services.screen_pen._resolve_target_rect", return_value=None):
        res = screen_pen.screen_pen_annotate(action="circle", label="NonexistentMenu")
        assert res["status"] == "error"
        assert res["code"] == "TARGET_NOT_FOUND"
