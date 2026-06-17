"""Integration: prompt_loan persists, surfaces in list, route updates it."""
import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app
from app.services import profiles as prof_svc


client = TestClient(app)


def test_prompt_loan_false_by_default(db_path):
    with get_db() as conn:
        p = prof_svc.create_profile(conn, "Test", "personal")
    assert p["prompt_loan"] == 0


def test_update_profile_via_patch_route(db_path):
    with get_db() as conn:
        p = prof_svc.create_profile(conn, "IncTest", "incorporation")
    pid = p["id"]

    resp = client.patch(f"/api/profiles/{pid}", json={"prompt_loan": True})
    assert resp.status_code == 200
    assert resp.json()["prompt_loan"] == 1

    resp2 = client.get("/api/profiles")
    profile = next(x for x in resp2.json() if x["id"] == pid)
    assert profile["prompt_loan"] == 1


def test_patch_unknown_profile_returns_404(db_path):
    resp = client.patch("/api/profiles/9999", json={"prompt_loan": True})
    assert resp.status_code == 404


def test_prompt_loan_toggle_off(db_path):
    with get_db() as conn:
        p = prof_svc.create_profile(conn, "IncOff", "incorporation")
        prof_svc.update_profile(conn, p["id"], prompt_loan=True)

    resp = client.patch(f"/api/profiles/{p['id']}", json={"prompt_loan": False})
    assert resp.status_code == 200
    assert resp.json()["prompt_loan"] == 0


def test_list_profiles_includes_prompt_loan_field(db_path):
    resp = client.get("/api/profiles")
    assert resp.status_code == 200
    for p in resp.json():
        assert "prompt_loan" in p
