"""Smoke-test that the system prompt includes prompt_loan guidance."""
from app.agent.prompts import system_prompt


def test_prompt_contains_loan_instruction_for_ui():
    prompt = system_prompt("ui")
    assert "prompt_loan" in prompt
    assert "personal pocket" in prompt.lower()


def test_prompt_contains_loan_instruction_for_whatsapp():
    prompt = system_prompt("whatsapp")
    assert "prompt_loan" in prompt
    assert "personal pocket" in prompt.lower()
