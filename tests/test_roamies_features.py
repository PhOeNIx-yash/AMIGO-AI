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
