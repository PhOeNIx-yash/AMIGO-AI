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


def test_roamies_cards_emission_and_action_cards_suppression():
    from amigo.utils.tool_registry import (
        build_action_cards,
        emit_roamies_card,
        _tool_get_weather,
        _tool_stock_quote,
        _tool_set_timer,
        _tool_set_reminder,
    )

    # 1. Action cards must be completely empty (suppressed in favor of Roamies overlay cards)
    cards = build_action_cards("get_weather", {"city": "Tokyo"}, {}, None, "Sunny", "weather")
    assert cards == []
    cards_stock = build_action_cards("stock_quote", {"symbol": "AAPL"}, {"price": 150.0}, None, "Apple quote", "stock")
    assert cards_stock == []

    # 2. Roamies card emission helper creates valid ink event mark with kind='card'
    with patch("amigo.services.screen_pen.push_overlay_event") as mock_push:
        emit_roamies_card(
            title="Weather · Paris",
            heading="22°C · Sunny",
            text="Mild breeze in Paris.",
            items=["Temp: 22°C", "Wind: 10 km/h"],
            tone="sky",
            duration=15.0,
        )
        assert mock_push.called
        event = mock_push.call_args[0][0]
        assert event["type"] == "ink"
        assert event["beats"][0]["marks"][0]["kind"] == "card"
        assert event["beats"][0]["marks"][0]["title"] == "Weather · Paris"
        assert event["beats"][0]["marks"][0]["heading"] == "22°C · Sunny"
        assert event["beats"][0]["marks"][0]["tone"] == "sky"
        assert len(event["beats"][0]["marks"][0]["items"]) == 2

    # 3. Weather tool emits interactive weather
    fake_weather = {
        "success": True,
        "city": "London",
        "temp_c": 18,
        "condition": "Cloudy",
        "humidity": 65,
        "wind_kmh": 14,
    }
    with patch("amigo.utils.tool_registry._cached_weather_data", return_value=fake_weather), \
         patch("amigo.utils.tool_registry.emit_interactive_weather") as mock_weather:
        res, _ = _tool_get_weather({"city": "London"}, "weather in London", "")
        assert "London" in res
        mock_weather.assert_called_once()
        assert mock_weather.call_args[0][0] == "London"

    # 4. Stock tool emits interactive stock with graph
    class DummyQuote:
        symbol = "MSFT"
        name = "Microsoft Corp"
        price = 420.50
        currency = "$"
        change = 5.25
        change_pct = 1.26
        previous_close = 415.25
        exchange = "NASDAQ"
        url = "https://finance.yahoo.com/quote/MSFT"
        day_high = 422.0
        day_low = 416.0
        volume = 20000000
        fifty_two_week_high = 450.0
        fifty_two_week_low = 310.0
        history = [415.0, 417.0, 420.5]
        timestamps = [1000, 2000, 3000]
        as_of = None

    with patch("amigo.utils.tool_registry.get_stock_quote", return_value=DummyQuote()), \
         patch("amigo.utils.tool_registry.emit_interactive_stock") as mock_stock:
        res, url, meta = _tool_stock_quote({"symbol": "MSFT"}, "MSFT price", "")
        assert "420.50" in res or "Microsoft" in res
        mock_stock.assert_called_once()
        assert mock_stock.call_args[0][0].symbol == "MSFT"

    # 5. Timer and Reminder tools emit Roamies interactive widgets
    with patch("amigo.utils.tool_registry.handle_set_timer", return_value="Timer set for 5 minutes."), \
         patch("amigo.utils.tool_registry.parse_relative_seconds", return_value=300), \
         patch("amigo.utils.tool_registry.emit_roamies_card") as mock_card:
        res, _ = _tool_set_timer({"duration": "5 minutes", "label": "Tea"}, "set timer for 5 minutes", "")
        assert "5 minutes" in res
        mock_card.assert_called_once()
        assert "Timer" in mock_card.call_args[1]["title"]
        assert mock_card.call_args[1]["tone"] == "teal"

    with patch("amigo.utils.tool_registry.handle_set_reminder", return_value="Reminder set"), \
         patch("amigo.utils.tool_registry.emit_interactive_reminders") as mock_rems:
        res, _, _ = _tool_set_reminder({"message": "Buy tea"}, "remind me to buy tea", "")
        assert "Reminder" in res
        mock_rems.assert_called_once()


def test_interactive_roamies_graph_bars_buttons():
    from amigo.utils.tool_registry import (
        emit_interactive_stock,
        emit_interactive_setting,
        emit_interactive_volume,
        emit_interactive_weather,
    )
    from amigo.core.reminder_timer import toggle_reminder_done, save_reminders, load_reminders

    # 1. Stock graph emission produces graph mark with points and axes
    class SampleQuote:
        symbol = "NVDA"
        name = "Nvidia Corp"
        price = 135.50
        currency = "$"
        change = 4.20
        change_pct = 3.20
        day_low = 131.0
        day_high = 136.0
        previous_close = 131.30
        exchange = "NASDAQ"
        volume = 50000000
        history = [131.3, 132.5, 134.0, 135.5]
        timestamps = [1, 2, 3, 4]

    with patch("amigo.services.screen_pen.push_overlay_event") as mock_push:
        emit_interactive_stock(SampleQuote())
        assert mock_push.called
        ink_events = [args[0][0] for args in mock_push.call_args_list if args[0][0].get("type") == "ink"]
        assert ink_events
        event = ink_events[0]
        assert len(event["beats"]) >= 2
        card_mark = event["beats"][0]["marks"][0]
        graph_mark = event["beats"][1]["marks"][0]
        assert card_mark["kind"] == "card"
        assert graph_mark["kind"] == "graph"
        assert len(graph_mark["plots"][0]["points"]) == 4
        assert graph_mark["dots"][0]["y"] == 135.50
        # Check avatar hype act and SFX are emitted
        emote_events = [args[0][0] for args in mock_push.call_args_list if args[0][0].get("type") == "emote"]
        assert any(e.get("action") == "hype" for e in emote_events)

    # 2. Interactive setting produces card + toggle button + sfx
    with patch("amigo.services.screen_pen.push_overlay_event") as mock_push:
        emit_interactive_setting("Bluetooth", "Bluetooth is currently active.", is_on=True)
        assert mock_push.called
        ink_events = [args[0][0] for args in mock_push.call_args_list if args[0][0].get("type") == "ink"]
        assert ink_events
        event = ink_events[0]
        btn_mark = event["beats"][1]["marks"][0]
        assert btn_mark["kind"] == "button"
        assert btn_mark["part"] == "act-toggle-bluetooth"
        assert "Turn Off" in btn_mark["text"]

    # 3. Interactive volume produces animated progress bar + stepper buttons
    with patch("amigo.services.screen_pen.push_overlay_event") as mock_push:
        emit_interactive_volume(60)
        assert mock_push.called
        event = mock_push.call_args[0][0]
        bars_mark = event["beats"][0]["marks"][0]
        btns = event["beats"][1]["marks"]
        assert bars_mark["kind"] == "bars"
        assert bars_mark["bars"][0]["value"] == 60
        assert any(b["part"] == "act-vol-down" for b in btns)
        assert any(b["part"] == "act-vol-up" for b in btns)

    # 4. Interactive weather produces card + atmospheric bars
    with patch("amigo.services.screen_pen.push_overlay_event") as mock_push:
        emit_interactive_weather("Berlin", 22, "Clear", 45, 12)
        assert mock_push.called
        event = mock_push.call_args[0][0]
        card = event["beats"][0]["marks"][0]
        bars = event["beats"][1]["marks"][0]
        assert card["kind"] == "card"
        assert bars["kind"] == "bars"
        assert len(bars["bars"]) == 3

    # 5. Interactive reminder toggle
    save_reminders([{"id": "test-rem-1", "message": "Call mom", "done": False}])
    assert toggle_reminder_done("test-rem-1") is True
    rems = load_reminders()
    assert rems[0]["done"] is True


def test_api_overlay_action_endpoint():
    from amigo.ui.server import app, _recent_overlay_actions
    _recent_overlay_actions.clear()

    client = app.test_client()

    # Passive events from Roamies MUST be ignored and never trigger toggles
    with patch("amigo.services.os_settings.toggle_setting") as mock_toggle:
        resp = client.post("/api/overlay/action", json={"type": "ink_closed", "part": "act-toggle-wi-fi"})
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ignored"
        mock_toggle.assert_not_called()

        resp2 = client.post("/api/overlay/action", json={"type": "draw_done", "part": ""})
        assert resp2.status_code == 200
        assert resp2.get_json()["status"] == "ignored"
        mock_toggle.assert_not_called()

    _recent_overlay_actions.clear()
    with patch("amigo.services.os_automation.volume_up") as mock_vup, \
         patch("amigo.services.os_automation.get_system_status", return_value={"volume": 70}), \
         patch("amigo.services.screen_pen.push_overlay_event"):
        resp = client.post("/api/overlay/action", json={"type": "ink_action", "part": "act-vol-up"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "success"
        mock_vup.assert_called_once()

    _recent_overlay_actions.clear()
    with patch("amigo.services.os_settings.toggle_setting", return_value=(True, "Bluetooth toggled")), \
         patch("amigo.services.os_settings.get_setting_state", return_value=(True, True, "Bluetooth is on")), \
         patch("amigo.services.screen_pen.push_overlay_event"):
        resp = client.post("/api/overlay/action", json={"type": "ink_action", "part": "act-toggle-bluetooth"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "success"
        assert data["enabled"] is True


def test_roamies_1_4_0_new_features():
    from amigo.services import screen_pen, hands
    from amigo.core.reminder_timer import handle_snooze_reminder, save_reminders, load_reminders
    from amigo.core.proactive_intelligence import start_focus_session, get_focus_status, generate_morning_briefing
    from amigo.ui.server import app, _recent_overlay_actions, _in_progress_actions
    import base64

    # 1. Test mind map card generation
    mm_res = screen_pen.screen_pen_annotate(
        action="mindmap",
        topic="Machine Learning",
        data={
            "title": "Machine Learning",
            "branches": [
                {"text": "Supervised", "items": ["Classification", "Regression"], "tone": "teal"},
                {"text": "Unsupervised", "items": ["Clustering", "PCA"], "tone": "sky"},
            ],
        },
    )
    assert mm_res["status"] == "success"
    mark = mm_res["event"]["beats"][0]["marks"][0]
    assert mark["kind"] == "mindmap"
    assert mark["title"] == "Machine Learning"
    assert len(mark["branches"]) == 2
    assert mark["branches"][0]["items"] == ["Classification", "Regression"]

    # 2. Test moving arrows (flow: True)
    arrow_res = screen_pen.screen_pen_annotate(action="arrow", x=200, y=300, x2=500, y2=600)
    assert arrow_res["status"] == "success"
    arr_mark = arrow_res["event"]["beats"][0]["marks"][0]
    assert arr_mark["kind"] == "arrow"
    assert arr_mark.get("flow") is True

    # 3. Test Qwen3-VL / Qwen3.5 0-to-1000 coordinate grid normalization
    # Normal 0-1 scale:
    p1 = hands.normalize_grid_coords(0.5, 0.5, bounds=[0, 0, 1920, 1080])
    assert p1 == (960, 540)
    # Qwen-VL 0-to-1000 grid:
    p2 = hands.normalize_grid_coords(500, 500, bounds=[0, 0, 1920, 1080])
    assert p2 == (960, 540)
    p3 = hands.normalize_grid_coords(250, 750, bounds=[100, 100, 1000, 800])
    assert p3 == (350, 700)
    # Raw pixels:
    p4 = hands.normalize_grid_coords(1440, 900)
    assert p4 == (1440, 900)

    # 4. Test Snooze by voice
    save_reminders([{"id": "snooze-test-1", "message": "Stretch legs", "target_time": "2026-11-16T10:00:00"}])
    msg = handle_snooze_reminder(duration_seconds=600.0)
    assert "Snoozed 'Stretch legs' for 10 minutes" in msg
    rems = load_reminders()
    assert any("Stretch legs" in r.get("message", "") for r in rems)

    # 5. Test Focus Mode
    f_msg = start_focus_session(duration_minutes=25, goal="Algorithm Design")
    assert "Focus mode active for 25 minutes" in f_msg
    st = get_focus_status()
    assert st["active"] is True
    assert st["goal"] == "Algorithm Design"

    # 6. Test Morning Briefing
    summary, items = generate_morning_briefing()
    assert "Good morning!" in summary
    assert len(items) >= 2

    # 7. Test Save Card As Image & Button Spamming In-Progress Lock
    client = app.test_client()
    _recent_overlay_actions.clear()
    _in_progress_actions.clear()

    # 1x1 transparent PNG base64
    tiny_png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
    with patch("pathlib.Path.write_bytes") as mock_write:
        save_resp = client.post("/api/overlay/action", json={
            "type": "card_image",
            "title": "Test MindMap",
            "data": tiny_png,
            "copied": True,
        })
        assert save_resp.status_code == 200
        assert save_resp.get_json()["status"] == "saved"
        mock_write.assert_called_once()

    # Rapid button spam lock test
    _in_progress_actions.add("ink_action:act-toggle-wi-fi")
    spam_resp = client.post("/api/overlay/action", json={
        "type": "ink_action",
        "part": "act-toggle-wi-fi",
    })
    assert spam_resp.status_code == 200
    assert spam_resp.get_json()["status"] == "in_progress"
    _in_progress_actions.clear()
