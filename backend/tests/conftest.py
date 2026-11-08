import os
import pytest
from sqlalchemy import create_engine
from fastapi.testclient import TestClient


@pytest.fixture
def env(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet

    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path}/test.sqlite"
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    if not url.startswith("sqlite") and not (parsed.database or "").startswith(
        "annotation_test"
    ):
        raise ValueError(
            "集成测试只允许 annotation_test 系列数据库，禁止使用业务主库。"
        )
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("SECRET_KEY", "test-only-signing-key-" + "x" * 40)
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "objects"))
    monkeypatch.setenv("REDIS_CACHE_URL", "redis://127.0.0.1:1/0")
    monkeypatch.setenv("REDIS_BROKER_URL", "redis://127.0.0.1:1/0")
    monkeypatch.setenv("MONGODB_URI", "mongodb://127.0.0.1:1")
    monkeypatch.setenv("MONGODB_DATABASE", "annotation_test_events")
    from app.config import settings
    from app.db import engine, Base
    import app.models

    settings.cache_clear()
    engine.cache_clear()
    from app.limits import fallback

    fallback.clear()
    Base.metadata.create_all(engine())
    yield
    Base.metadata.drop_all(engine())
    engine().dispose()
    engine.cache_clear()
    settings.cache_clear()


@pytest.fixture
def client(env):
    from app.main import app

    with TestClient(app) as client:
        yield client


@pytest.fixture
def accounts(client):
    from app.db import session_scope
    from app.models import User
    from app.security import passwords

    with session_scope() as db:
        db.add(
            User(
                username="admin",
                password_hash=passwords.hash("test-password"),
                is_admin=True,
            )
        )

    def login(name):
        response = client.post(
            "/api/v1/auth/login", json={"username": name, "password": "test-password"}
        )
        assert response.status_code == 200, response.text
        return {"Authorization": "Bearer " + response.json()["access_token"]}

    admin = login("admin")
    ids = {}
    for name in ("owner", "reviewer", "outsider"):
        response = client.post(
            "/api/v1/admin/users",
            headers=admin,
            json={"username": name, "password": "test-password"},
        )
        assert response.status_code == 201, response.text
        ids[name] = response.json()["id"]
    return {
        "headers": {name: login(name) for name in ("owner", "reviewer", "outsider")},
        "admin": admin,
        "ids": ids,
        "login": login,
    }


@pytest.fixture
def workspace(client, accounts):
    def make(task="text_classification"):
        headers = accounts["headers"]["owner"]
        response = client.post(
            "/api/v1/projects",
            headers=headers,
            json={"name": "测试项目", "task_type": task},
        )
        assert response.status_code == 201, response.text
        project = response.json()
        response = client.post(
            f"/api/v1/projects/{project['id']}/schemas",
            headers=headers,
            json={"labels": [{"name": "A"}, {"name": "B"}]},
        )
        assert response.status_code == 201, response.text
        schema = response.json()
        assert (
            client.post(
                f"/api/v1/schemas/{schema['id']}/publish", headers=headers
            ).status_code
            == 200
        )
        response = client.post(
            f"/api/v1/projects/{project['id']}/datasets", headers=headers
        )
        assert response.status_code == 200, response.text
        return project, schema, response.json(), headers

    return make
