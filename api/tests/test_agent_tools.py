from app.agent.tools import build_tools


def test_import_tools_registered():
    names = {t.name for t in build_tools("ui", lambda spec: None, "ui")}
    assert {"get_import_summary", "remap_import", "approve_import"} <= names


def test_import_tools_registered_on_whatsapp_channel_too():
    names = {t.name for t in build_tools("whatsapp", lambda spec: None, "whatsapp")}
    assert {"get_import_summary", "remap_import", "approve_import"} <= names


def test_prompt_mentions_import_gates():
    from app.agent.prompts import system_prompt
    text = system_prompt("ui")
    assert "get_import_summary" in text
    assert "confirm" in text.lower()
