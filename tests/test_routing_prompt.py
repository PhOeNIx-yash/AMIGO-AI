from amigo.utils.tool_registry import get_static_master_prompt


def test_prompt_uses_intent_instead_of_fixed_keyword_triggers():
    prompt = get_static_master_prompt()

    assert "not by matching fixed words or memorized example phrases" in prompt
    assert "DISAMBIGUATION EXAMPLES" not in prompt
    assert '"what is X"' not in prompt
    assert '"who is Y"' not in prompt


def test_prompt_searches_for_external_facts_amigo_cannot_reliably_answer():
    prompt = get_static_master_prompt()

    assert "uncertain, the subject is unfamiliar or specialized" in prompt
    assert "answer from retrieved results" in prompt
    assert "say so rather than guessing" in prompt
    assert "Do not send every factual question to web search." in prompt


def test_prompt_preserves_specialized_tools_and_json_output_contract():
    prompt = get_static_master_prompt()

    assert "Use dedicated tools for information that must come from the current PC" in prompt
    assert "Use document_qa for questions about the user's indexed personal files" in prompt
    assert "Return only valid JSON" in prompt


def test_tool_list_exposes_local_clock_and_distinguishes_media_actions():
    prompt = get_static_master_prompt()

    assert "get_time(): Read the current local system time" in prompt
    assert "get_date(): Read the current local date" in prompt
    assert "next_track(): Advance active media playback" in prompt
    assert "current_media(): Report the title and playback state" in prompt
