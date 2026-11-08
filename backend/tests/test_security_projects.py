from uuid import uuid4
from datetime import timedelta
import pytest
from sqlalchemy import select
from app.db import session_scope
from app.models import User, RefreshSession, now
from test_maturity_regressions import text_item


def test_csrf_logout_password_and_disabled_sessions(client, accounts):
    for route in ("login", "refresh", "logout"):
        response = client.post(
            "/api/v1/auth/" + route,
            headers={"Origin": "https://untrusted.example"},
            json={"username": "owner", "password": "test-password"}
            if route == "login"
            else None,
        )
        assert response.status_code == 403
    owner = accounts["headers"]["owner"]
    assert (
        client.post(
            "/api/v1/auth/password",
            headers=owner,
            json={"current_password": "wrong", "new_password": "new-password"},
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/auth/password",
            headers=owner,
            json={"current_password": "test-password", "new_password": "new-password"},
        ).status_code
        == 200
    )
    assert client.get("/api/v1/auth/me", headers=owner).status_code == 401
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"username": "owner", "password": "test-password"},
        ).status_code
        == 401
    )
    response = client.post(
        "/api/v1/auth/login", json={"username": "owner", "password": "new-password"}
    )
    assert response.status_code == 200
    fresh = {"Authorization": "Bearer " + response.json()["access_token"]}
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.get("/api/v1/auth/me", headers=fresh).status_code == 401
    assert (
        client.patch(
            "/api/v1/admin/users/" + accounts["ids"]["reviewer"],
            headers=accounts["admin"],
            params={"enabled": False},
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/api/v1/auth/me", headers=accounts["headers"]["reviewer"]
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/admin/users",
            headers=fresh,
            json={"username": "bad", "password": "test-password"},
        ).status_code
        == 401
    )


def test_refresh_expiry_and_non_admin_access(client, accounts):
    with session_scope() as db:
        for entry in db.scalars(select(RefreshSession)).all():
            entry.expires_at = now() - timedelta(days=1)
    assert client.post("/api/v1/auth/refresh").status_code == 401
    assert (
        client.get(
            "/api/v1/admin/users", headers=accounts["headers"]["owner"]
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/admin/users",
            headers=accounts["admin"],
            json={"username": "weak", "password": "short"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/admin/users",
            headers=accounts["admin"],
            json={"username": "owner", "password": "test-password"},
        ).status_code
        == 409
    )


def test_login_rate_limit_and_production_failure_closed(client, monkeypatch):
    for _ in range(9):
        assert (
            client.post(
                "/api/v1/auth/login", json={"username": "missing", "password": "wrong"}
            ).status_code
            == 401
        )
    assert (
        client.post(
            "/api/v1/auth/login", json={"username": "missing", "password": "wrong"}
        ).status_code
        == 429
    )
    from app.config import settings

    monkeypatch.setenv("APP_ENV", "production")
    settings.cache_clear()
    assert (
        client.post(
            "/api/v1/auth/login", json={"username": "another", "password": "wrong"}
        ).status_code
        == 503
    )


def test_project_statistics_archive_and_role_isolation(client, accounts, workspace):
    project, schema, dataset, headers, item = text_item(client, workspace)
    detail = client.get("/api/v1/projects/" + project["id"], headers=headers).json()
    assert detail["work_counts"]["unstarted"] == 1 and detail["item_count"] == 1
    outsider = accounts["headers"]["outsider"]
    assert not client.get("/api/v1/projects", headers=outsider).json()
    assert (
        client.post(
            "/api/v1/projects/" + project["id"] + "/archive", headers=outsider
        ).status_code
        == 404
    )
    assert (
        client.get(
            "/api/v1/projects/" + project["id"] + "/reviews", headers=outsider
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/projects/" + project["id"] + "/archive", headers=headers
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/projects/" + project["id"] + "/texts",
            headers=headers,
            json={"dataset_id": dataset["id"], "text": "blocked"},
        ).status_code
        == 409
    )
    assert (
        client.get("/api/v1/projects/" + project["id"], headers=headers).status_code
        == 200
    )


def test_schema_stable_ids_draft_update_and_filters(client, workspace):
    project, schema, _, headers, _ = text_item(client, workspace)
    draft = client.post(
        "/api/v1/projects/" + project["id"] + "/schemas",
        headers=headers,
        json={"labels": [{"name": "A"}, {"name": "C"}]},
    ).json()
    assert draft["labels"][0]["id"] == schema["labels"][0]["id"]
    assert (
        client.put(
            "/api/v1/schemas/" + draft["id"],
            headers=headers,
            json={"labels": draft["labels"], "rules": {"instructions": "新规则"}},
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/api/v1/projects/" + project["id"] + "/items",
            headers=headers,
            params={"state": "invalid"},
        ).status_code
        == 400
    )
    assert (
        client.get(
            "/api/v1/projects/" + project["id"] + "/items",
            headers=headers,
            params={"limit": 201},
        ).status_code
        == 422
    )
    response = client.post(
        "/api/v1/projects/" + project["id"] + "/schemas",
        headers=headers,
        json={"labels": [{"name": "A"}, {"name": "A"}]},
    )
    assert response.status_code == 400


def test_idempotency_key_is_bounded_before_database(client, workspace):
    project, _, _, headers = workspace()
    assert (
        client.post(
            "/api/v1/projects/" + project["id"] + "/imports",
            headers={**headers, "Idempotency-Key": "x" * 101},
            files={"file": ("a.jsonl", b'{"text":"sample"}\n')},
        ).status_code
        == 422
    )


def test_validation_errors_do_not_echo_passwords(client):
    response = client.post(
        "/api/v1/auth/login", json={"username": "valid", "password": "x" * 300}
    )
    assert (
        response.status_code == 422
        and response.json()["error"]["code"] == "VALIDATION_ERROR"
    )
    assert "x" * 300 not in response.text
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


def test_item_list_uses_one_batch_work_query(client, workspace):
    from sqlalchemy import event
    from app.db import engine

    project, _, dataset, headers = workspace()
    for i in range(25):
        client.post(
            "/api/v1/projects/" + project["id"] + "/texts",
            headers=headers,
            json={"dataset_id": dataset["id"], "text": str(i)},
        )
    queries = []

    def collect(connection, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    event.listen(engine(), "before_cursor_execute", collect)
    try:
        response = client.get(
            "/api/v1/projects/" + project["id"] + "/items", headers=headers
        )
    finally:
        event.remove(engine(), "before_cursor_execute", collect)
    assert len(response.json()) == 25
    assert sum("FROM work_states" in query for query in queries) == 1


def test_claim_expiry_release_and_submit_clear_lease(client, accounts, workspace):
    from app.models import Work
    from test_workflow import save

    project, schema, _, headers, item = text_item(client, workspace)
    client.post(
        "/api/v1/projects/" + project["id"] + "/members",
        headers=headers,
        params={"user_id": accounts["ids"]["outsider"], "role": "annotator"},
    )
    client.post(
        "/api/v1/items/" + item["id"] + "/claim",
        headers=headers,
        params={"schema_id": schema["id"]},
    )
    with session_scope() as db:
        work = db.scalar(select(Work).where(Work.item_id == item["id"]))
        work.claim_expires_at = now() - timedelta(seconds=1)
    outsider = accounts["headers"]["outsider"]
    response = client.post(
        "/api/v1/items/" + item["id"] + "/claim",
        headers=outsider,
        params={"schema_id": schema["id"]},
    )
    assert response.status_code == 200
    assert (
        client.delete(
            "/api/v1/items/" + item["id"] + "/claim",
            headers=headers,
            params={"schema_id": schema["id"]},
        ).status_code
        == 403
    )
    saved = save(client, item, schema, outsider, response.json()["version"])[0].json()
    submit = client.post(
        "/api/v1/items/" + item["id"] + "/submit",
        headers=outsider,
        json={
            "schema_id": schema["id"],
            "revision_id": saved["revision"]["id"],
            "expected_version": saved["work"]["version"],
        },
    )
    assert submit.status_code == 200 and submit.json()["claim_user_id"] is None
