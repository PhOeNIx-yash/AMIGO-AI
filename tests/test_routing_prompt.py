from amigo.utils.tool_registry import get_static_master_prompt


def test_prompt_uses_intent_instead_of_fixed_keyword_triggers():
    prompt = get_static_master_prompt()

    assert "DISAMBIGUATION EXAMPLES" not in prompt
    assert '"what is X"' not in prompt
    assert '"who is Y"' not in prompt
    assert "Understand user intent and select the single best tool" in prompt


def test_prompt_searches_for_external_facts_amigo_cannot_reliably_answer():
    prompt = get_static_master_prompt()

    assert "web_search" in prompt
    assert "Search the web" in prompt
    assert "document_qa" in prompt


def test_prompt_preserves_specialized_tools_and_json_output_contract():
    prompt = get_static_master_prompt()

    assert "document_qa" in prompt
    assert "CORE OPERATING PRINCIPLES:" in prompt
    assert '{"actions": [{"tool":' in prompt


def test_tool_list_exposes_local_clock_and_distinguishes_media_actions():
    prompt = get_static_master_prompt()

    assert "time_date(" in prompt
    assert "media_control(" in prompt
    assert "power_control(" in prompt
    assert "play_youtube(" in prompt
