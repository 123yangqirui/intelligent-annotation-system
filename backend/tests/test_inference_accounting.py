from uuid import uuid4
from types import SimpleNamespace
import httpx, pytest
from sqlalchemy import select
from app.db import session_scope
from app.models import (
    Provider,
    ProviderGrant,
    ModelSpec,
    ModelCall,
    Job,
    User,
    Project,
    LabelSchema,
    Item,
)
from app.security import crypt
from app import inference, tasks
from test_maturity_regressions import text_item


def context(client, accounts, workspace):
    project, schema, dataset, headers, item = text_item(client, workspace)
    with session_scope() as db:
        provider = Provider(
            name="fixture",
            base_url="http://127.0.0.1:8101/v1",
            model_name="fixture",
            encrypted_key=crypt().encrypt(b"key").decode(),
        )
        db.add(provider)
        db.flush()
        db.add(ProviderGrant(provider_id=provider.id, project_id=project["id"]))
        model = ModelSpec(
            name="fixture",
            adapter="llm",
            task_type="text_classification",
            provider_id=provider.id,
            config={},
        )
        db.add(model)
        db.flush()
        job = tasks.create_job(
            db,
            db.get(User, accounts["ids"]["owner"]),
            project["id"],
            "prediction",
            {"model_id": model.id, "schema_id": schema["id"]},
            str(uuid4()),
            [item["id"]],
        )
        return project, schema, item, headers, provider.id, model.id, job.id


@pytest.mark.parametrize(
    "usage,status",
    [
        ({"total_tokens": 21}, "succeeded"),
        (None, "usage_unknown"),
        ({"total_tokens": -1}, "usage_unknown"),
        ({"total_tokens": True}, "usage_unknown"),
    ],
)
def test_usage_settlement_is_conservative(
    client, accounts, workspace, monkeypatch, usage, status
):
    project, schema, item, headers, pid, mid, jid = context(client, accounts, workspace)

    def response(*a, **kw):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"label_id":"'
                            + schema["labels"][0]["id"]
                            + '"}'
                        }
                    }
                ],
                "usage": usage,
            },
        )

    monkeypatch.setattr(inference.httpx, "post", response)
    tasks.execute_job.run(jid)
    with session_scope() as db:
        call = db.scalar(select(ModelCall))
        provider = db.get(Provider, pid)
        assert call.status == status
        assert provider.spent_tokens == (21 if status == "succeeded" else 0)
        assert (provider.reserved_tokens > 0) == (status == "usage_unknown")
        if status == "usage_unknown":
            call_id = call.id
    if status == "usage_unknown":
        response = client.post(
            "/api/v1/admin/model-calls/" + call_id + "/reconcile",
            headers=accounts["admin"],
            params={"total_tokens": 7},
        )
        assert response.status_code == 200
        assert (
            client.post(
                "/api/v1/admin/model-calls/" + call_id + "/reconcile",
                headers=accounts["admin"],
                params={"total_tokens": 7},
            ).status_code
            == 409
        )
        with session_scope() as db:
            assert (
                db.get(Provider, pid).spent_tokens == 7
                and db.get(Provider, pid).reserved_tokens == 0
            )


@pytest.mark.parametrize("code,expected", [(429, "rejected"), (500, "usage_unknown")])
def test_provider_failure_budget_and_failed_job(
    client, accounts, workspace, monkeypatch, code, expected
):
    _, _, _, _, pid, _, jid = context(client, accounts, workspace)
    monkeypatch.setattr(inference.httpx, "post", lambda *a, **kw: httpx.Response(code))
    tasks.execute_job.run(jid)
    with session_scope() as db:
        assert db.get(Job, jid).status == "failed"
        assert db.scalar(select(ModelCall)).status == expected
        assert (db.get(Provider, pid).reserved_tokens > 0) == (
            expected == "usage_unknown"
        )


def test_http_plugin_validates_contract_and_uses_cache(client, workspace, monkeypatch):
    project, schema, _, _, item = text_item(client, workspace)
    cached = {}
    calls = []

    class Cache:
        def get(self, key):
            return cached.get(key)

        def set(self, key, value, ex):
            cached[key] = value
            assert ex > 0

    monkeypatch.setattr(inference.Redis, "from_url", lambda *a, **kw: Cache())

    def response(url, **kw):
        calls.append(kw["json"])
        assert "reference" not in kw["json"]
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "content": {
                    "task_type": "text_classification",
                    "label_id": schema["labels"][0]["id"],
                },
                "signals": {"score": 0.8},
            },
        )

    monkeypatch.setattr(inference.httpx, "post", response)
    with session_scope() as db:
        model = ModelSpec(
            name="http",
            adapter="http",
            task_type="text_classification",
            config={"endpoint": "http://127.0.0.1:8101"},
        )
        db.add(model)
        db.flush()
        args = (
            db,
            model,
            SimpleNamespace(project_id=project["id"]),
            db.get(Project, project["id"]),
            db.get(LabelSchema, schema["id"]),
            db.get(Item, item["id"]),
        )
        assert inference.predict(*args)[1]["score"] == 0.8
        assert inference.predict(*args)[1]["cached"] is True and len(calls) == 1
        for key in cached:
            cached[key] = "malformed json"
        assert (
            inference.predict(*args)[0]["label_id"] == schema["labels"][0]["id"]
            and len(calls) == 2
        )


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/model",
        "http://evil.example",
        "http://user:secret@localhost",
        "http://localhost?key=secret",
    ],
)
def test_endpoint_rejects_unapproved_input(env, url):
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        inference.validate_url(url)
