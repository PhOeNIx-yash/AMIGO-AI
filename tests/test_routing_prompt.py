from amigo.utils.tool_registry import get_static_master_prompt


def test_prompt_uses_intent_instead_of_fixed_keyword_triggers():
    prompt = get_static_master_prompt()

    assert "DISAMBIGUATION EXAMPLES" not in prompt
    assert '"what is X"' not in prompt
    assert '"who is Y"' not in prompt
    assert "Resolve pronouns ('it', 'that', 'they', 'he', 'she', 'there') and follow-up questions" in prompt


def test_prompt_searches_for_external_facts_amigo_cannot_reliably_answer():
    prompt = get_static_master_prompt()

    assert "web_search" in prompt
    assert "Search the web for external information, current events, public facts" in prompt
    assert "Only reference 'available documents' or 'in your files' if the user specifically asked about their personal files." in prompt


def test_prompt_preserves_specialized_tools_and_json_output_contract():
    prompt = get_static_master_prompt()

    assert "document_qa" in prompt
    assert "ROUTING RULES" in prompt or "CORE RULES:" in prompt
    assert '{"actions": [{"tool":' in prompt


def test_tool_list_exposes_local_clock_and_distinguishes_media_actions():
    prompt = get_static_master_prompt()

    assert "get_time(): Read the current local system time" in prompt
    assert "get_date(): Read the current local date" in prompt
    assert "next_track(): Advance active media playback" in prompt
    assert "current_media(): Report the title and playback state" in prompt
