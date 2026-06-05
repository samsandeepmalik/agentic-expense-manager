from app.services import chat_store


def test_session_lifecycle(conn):
    session = chat_store.create_session(conn, channel="ui")
    assert session["title"] == "New chat"

    chat_store.add_message(conn, session["id"], "user", {"text": "hello"})
    chat_store.add_message(conn, session["id"], "assistant",
                           {"text": "hi", "ui_specs": []})
    messages = chat_store.list_messages(conn, session["id"])
    assert [m["role"] for m in messages] == ["user", "assistant"]

    # First user message becomes the title
    sessions = chat_store.list_sessions(conn)
    assert sessions[0]["title"] == "hello"

    chat_store.delete_session(conn, session["id"])
    assert chat_store.list_sessions(conn) == []


def test_list_sessions_channel_filter(conn):
    ui = chat_store.create_session(conn, channel="ui")
    wa = chat_store.ensure_session(conn, "wa:123@s.whatsapp.net", "whatsapp")
    assert [s["id"] for s in chat_store.list_sessions(conn)] == [ui["id"]]
    assert [s["id"] for s in chat_store.list_sessions(conn, channel="whatsapp")] == [wa["id"]]
    assert len(chat_store.list_sessions(conn, channel=None)) == 2
