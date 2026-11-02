"""Creating categories: duplicates are a clean 409, scoped to the user.

The table's UniqueConstraint can't catch duplicate parents (SQLite treats NULL
parent_id values as distinct) and turned duplicate children into a 500, so the
service checks names itself.
"""

import pytest


@pytest.fixture
def client(clean_db):
    from fastapi.testclient import TestClient

    from backend import settings
    from backend.auth import hash_password, mint_session
    from backend.main import app
    from backend.models import User

    clean_db.add(User(username="t", password_hash=hash_password("x" * 12)))
    clean_db.add(User(username="other", password_hash=hash_password("x" * 12)))
    clean_db.commit()

    def as_user(username):
        c = TestClient(app)
        c.cookies.set("money_session", mint_session(settings.MONEY_SESSION_SECRET, username))
        return c

    yield as_user


def test_create_returns_the_new_category(client):
    c = client("t")
    r = c.post("/api/categories", json={"name": "  Pets ", "kind": "expense"})
    assert r.status_code == 201
    assert r.json()["name"] == "Pets"
    assert any(cat["name"] == "Pets" for cat in c.get("/api/categories").json())


def test_duplicate_parent_is_409_whatever_the_case(client):
    c = client("t")
    assert c.post("/api/categories", json={"name": "Pets", "kind": "expense"}).status_code == 201
    r = c.post("/api/categories", json={"name": "pets", "kind": "expense"})
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"]


def test_same_name_in_the_other_kind_is_fine(client):
    c = client("t")
    assert c.post("/api/categories", json={"name": "Gifts", "kind": "expense"}).status_code == 201
    assert c.post("/api/categories", json={"name": "Gifts", "kind": "income"}).status_code == 201


def test_another_user_can_reuse_the_name(client):
    assert client("t").post("/api/categories", json={"name": "Pets", "kind": "expense"}).status_code == 201
    assert client("other").post("/api/categories", json={"name": "Pets", "kind": "expense"}).status_code == 201


def test_child_inherits_and_duplicate_child_is_409_not_500(client):
    c = client("t")
    parent = c.post("/api/categories", json={"name": "Pets", "kind": "expense"}).json()
    child = c.post("/api/categories", json={"name": "Vet", "kind": "income", "parent_id": parent["id"]})
    assert child.status_code == 201
    assert child.json()["kind"] == "expense"
    assert child.json()["color_slot"] == parent["color_slot"]
    r = c.post("/api/categories", json={"name": "Vet", "kind": "expense", "parent_id": parent["id"]})
    assert r.status_code == 409


def test_no_third_level(client):
    c = client("t")
    parent = c.post("/api/categories", json={"name": "Pets", "kind": "expense"}).json()
    child = c.post("/api/categories", json={"name": "Vet", "parent_id": parent["id"]}).json()
    r = c.post("/api/categories", json={"name": "Shots", "parent_id": child["id"]})
    assert r.status_code == 422


def test_cannot_nest_under_another_users_category(client):
    theirs = client("other").post("/api/categories", json={"name": "Pets", "kind": "expense"}).json()
    r = client("t").post("/api/categories", json={"name": "Vet", "parent_id": theirs["id"]})
    assert r.status_code == 422


def test_blank_name_is_rejected(client):
    r = client("t").post("/api/categories", json={"name": "   ", "kind": "expense"})
    assert r.status_code == 422
