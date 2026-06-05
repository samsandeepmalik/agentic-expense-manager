from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.errors import AppError, register_error_handler


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
