import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.errors import AppError, register_error_handler


@pytest.fixture()
def client(db_path):
    from app.routes import categories as categories_routes

    app = FastAPI()
    register_error_handler(app)
    app.include_router(categories_routes.router)
    return TestClient(app, raise_server_exceptions=False)


def make_app():
    app = FastAPI()
    register_error_handler(app)

    @app.get("/boom")
    def boom():
        raise AppError("not_found", "Thing missing", 404)

    @app.get("/crash")
    def crash():
        raise RuntimeError("secret traceback")

    return app


def test_app_error_contract():
    client = TestClient(make_app(), raise_server_exceptions=False)
    response = client.get("/boom")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Thing missing"}}


def test_unexpected_error_hidden():
    client = TestClient(make_app(), raise_server_exceptions=False)
    response = client.get("/crash")
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "internal"
    assert "secret" not in body["error"]["message"]


def test_category_crud_with_taxable_and_budget(client):
    response = client.post("/api/categories", json={
        "name": "Coffee", "type": "expense", "percent": 50,
        "taxable": True, "budget_monthly": 80,
    })
    assert response.status_code == 200
    created = response.json()
    assert created["percent"] == 50 and created["budget_monthly"] == 80

    listing = client.get("/api/categories").json()
    assert any(c["name"] == "Coffee" for c in listing)

    assert client.delete(f"/api/categories/{created['id']}").json() == {"ok": True}


def test_tax_profile_activate(client):
    response = client.post("/api/tax-profiles", json={
        "name": "Ontario", "components": [{"name": "HST", "rate": 13.0}],
        "activate": True,
    })
    assert response.status_code == 200
    profiles = client.get("/api/tax-profiles").json()
    active = [p for p in profiles if p["is_active"]]
    assert len(active) == 1 and active[0]["name"] == "Ontario"
