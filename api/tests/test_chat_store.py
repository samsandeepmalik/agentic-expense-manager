from app.services import chat_store


# ---------------------------------------------------------------------------
# list_messages — limit parameter
# ---------------------------------------------------------------------------

def test_list_messages_limit_returns_last_n_in_chronological_order(conn):
    """limit=30 on a 35-message session returns the 30 most recent, oldest first."""
    session = chat_store.create_session(conn, channel="ui")
    sid = session["id"]
    for i in range(35):
        role = "user" if i % 2 == 0 else "assistant"
        chat_store.add_message(conn, sid, role, {"text": f"msg {i}"})

    limited = chat_store.list_messages(conn, sid, limit=30)
    assert len(limited) == 30
    # Must be in ascending (chronological) order despite the DESC fetch
    ids = [m["id"] for m in limited]
    assert ids == sorted(ids)
    # The 30 returned must be the last 30 (highest ids), not the first 30
    all_msgs = chat_store.list_messages(conn, sid)
    assert limited == all_msgs[-30:]


def test_list_messages_limit_larger_than_total_returns_all(conn):
    """limit > total message count returns all messages without error."""
    session = chat_store.create_session(conn, channel="ui")
    sid = session["id"]
    for i in range(5):
        chat_store.add_message(conn, sid, "user", {"text": f"msg {i}"})

    result = chat_store.list_messages(conn, sid, limit=100)
    assert len(result) == 5
    # Still chronological
    ids = [m["id"] for m in result]
    assert ids == sorted(ids)


def test_list_messages_no_limit_returns_all(conn):
    """limit=None (default) returns every message in ascending order."""
    session = chat_store.create_session(conn, channel="ui")
    sid = session["id"]
    for i in range(10):
        chat_store.add_message(conn, sid, "user", {"text": f"msg {i}"})

    result = chat_store.list_messages(conn, sid)
    assert len(result) == 10
    ids = [m["id"] for m in result]
    assert ids == sorted(ids)


def test_list_messages_limit_zero_returns_empty(conn):
    """limit=0 is a valid SQL LIMIT and should return no rows."""
    session = chat_store.create_session(conn, channel="ui")
    sid = session["id"]
    chat_store.add_message(conn, sid, "user", {"text": "hello"})

    result = chat_store.list_messages(conn, sid, limit=0)
    assert result == []


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
