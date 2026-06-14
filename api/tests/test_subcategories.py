import sqlite3

import pytest

from app.services import categories as cat_svc
from app.services import profiles as prof_svc


def test_categories_have_parent_id_column(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(categories)")}
    assert "parent_id" in cols


def test_same_child_name_allowed_under_different_parents(conn):
    food = cat_svc.upsert_category(conn, "Food", "expense", 100.0, True, None)
    travel = cat_svc.upsert_category(conn, "Travel", "expense", 100.0, True, None)
    a = cat_svc.upsert_category(conn, "Misc", "expense", 100.0, True, None,
                                parent_id=food["id"])
    b = cat_svc.upsert_category(conn, "Misc", "expense", 100.0, True, None,
                                parent_id=travel["id"])
    assert a["id"] != b["id"]


def test_duplicate_top_level_name_still_conflates(conn):
    first = cat_svc.upsert_category(conn, "Gym", "expense", 100.0, True, 50.0)
    again = cat_svc.upsert_category(conn, "Gym", "expense", 100.0, True, 80.0)
    assert first["id"] == again["id"]
    assert again["budget_monthly"] == 80.0


@pytest.mark.asyncio
async def test_manage_categories_accepts_parent(db_path):
    from app.agent.tools import build_tools
    from app.db import get_db

    tools = build_tools("ui", lambda spec: None, "ui")
    manage = next(t for t in tools if t.name == "manage_categories")
    with get_db() as conn:
        parent = cat_svc.upsert_category(conn, "Food", "expense", 100.0, True, None)
    res = await manage.execute("c", {"action": "upsert", "name": "Snacks",
                                     "type": "expense", "parent_id": parent["id"]})
    assert res.details.get("parent_id") == parent["id"]


def test_reparent_moves_category_under_a_parent(conn):
    food = cat_svc.upsert_category(conn, "Food", "expense", 100.0, True, None)
    snacks = cat_svc.upsert_category(conn, "Snacks", "expense", 100.0, True, None)
    moved = cat_svc.update_category(conn, snacks["id"], parent_id=food["id"])
    assert moved["parent_id"] == food["id"]


def test_reparent_promote_to_top_level(conn):
    food = cat_svc.upsert_category(conn, "Food", "expense", 100.0, True, None)
    snacks = cat_svc.upsert_category(conn, "Snacks", "expense", 100.0, True, None,
                                     parent_id=food["id"])
    promoted = cat_svc.update_category(conn, snacks["id"], parent_id=0)
    assert promoted["parent_id"] == 0


def test_reparent_rejects_nesting_under_a_child(conn):
    import pytest
    from app.errors import AppError
    food = cat_svc.upsert_category(conn, "Food", "expense", 100.0, True, None)
    coffee = cat_svc.upsert_category(conn, "Coffee", "expense", 100.0, True, None,
                                     parent_id=food["id"])
    misc = cat_svc.upsert_category(conn, "Misc", "expense", 100.0, True, None)
    with pytest.raises(AppError) as got:
        cat_svc.update_category(conn, misc["id"], parent_id=coffee["id"])
    assert got.value.code == "invalid_parent"


def test_reparent_rejects_moving_a_parent_with_children(conn):
    import pytest
    from app.errors import AppError
    food = cat_svc.upsert_category(conn, "Food", "expense", 100.0, True, None)
    cat_svc.upsert_category(conn, "Coffee", "expense", 100.0, True, None,
                            parent_id=food["id"])
    other = cat_svc.upsert_category(conn, "Travel", "expense", 100.0, True, None)
    with pytest.raises(AppError) as got:
        cat_svc.update_category(conn, food["id"], parent_id=other["id"])
    assert got.value.code == "has_children"


def test_reparent_scoped_to_profile(conn):
    import pytest
    from app.errors import AppError
    inc = prof_svc.create_profile(conn, "Inc", "incorporation")
    prof_svc.set_active(conn, inc["id"])
    sub = cat_svc.upsert_category(conn, "Subby", "expense", 100.0, True, None)
    prof_svc.set_active(conn, 1)
    with pytest.raises(AppError) as got:
        cat_svc.update_category(conn, sub["id"], parent_id=0)
    assert got.value.code == "category_not_found"


def test_patch_route_reparents(db_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routes import categories as cat_routes
    from app.db import get_db

    app = FastAPI()
    app.include_router(cat_routes.router)
    client = TestClient(app)

    with get_db() as conn:
        food = cat_svc.upsert_category(conn, "Food", "expense", 100.0, True, None)
        snacks = cat_svc.upsert_category(conn, "Snacks", "expense", 100.0, True, None)
        conn.commit()
    resp = client.patch(f"/api/categories/{snacks['id']}",
                        json={"parent_id": food["id"]})
    assert resp.status_code == 200
    assert resp.json()["parent_id"] == food["id"]
