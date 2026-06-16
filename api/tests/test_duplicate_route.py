def _payload(**over):
    return {"date": "2026-06-05", "type": "expense", "category": "Groceries",
            "total": 50.0, "merchant": "Metro"} | over


def test_duplicate_post_returns_409_with_details(db_path):
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    assert client.post("/api/transactions", json=_payload()).status_code == 200
    r = client.post("/api/transactions", json=_payload())
    assert r.status_code == 409
    body = r.json()["error"]
    assert body["code"] == "duplicate_suspected"
    assert body["details"]["txn"]["merchant"] == "Metro"


def test_confirm_duplicate_post_inserts(db_path):
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    client.post("/api/transactions", json=_payload())
    r = client.post("/api/transactions", json=_payload(confirm_duplicate=True))
    assert r.status_code == 200
    assert r.json()["id"] > 0
