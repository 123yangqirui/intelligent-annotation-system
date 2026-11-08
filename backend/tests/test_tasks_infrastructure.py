import json
from datetime import timedelta
from uuid import uuid4
from types import SimpleNamespace
from sqlalchemy import select, update
import pytest
from app.db import session_scope, new_session
from app.models import Job, JobItem, User, Outbox, Project, Revision, now
from app import tasks
from app.job_control import check_active, JobCancelled, LeaseLost
from test_maturity_regressions import text_item


def make_job(db, accounts, project, kind="invalid", config=None, ids=None):
    return tasks.create_job(
        db,
        db.get(User, accounts["ids"]["owner"]),
        project["id"],
        kind,
        config or {},
        str(uuid4()),
        ids,
    )


def test_job_cancellation_after_model_call_does_not_save_revision(
    client, accounts, workspace, monkeypatch
):
    from app import inference
    from app.models import ModelSpec

    project, schema, _, headers, item = text_item(client, workspace)
    with session_scope() as db:
        model = ModelSpec(
            name="mock", adapter="http", task_type="text_classification", config={}
        )
        db.add(model)
        db.flush()
        job = make_job(
            db,
            accounts,
            project,
            "prediction",
            {"schema_id": schema["id"], "model_id": model.id},
            [item["id"]],
        )
        job_id = job.id

    def predict(*args):
        with session_scope() as other:
            other.execute(
                update(Job).where(Job.id == job_id).values(status="cancel_requested")
            )
        return {
            "task_type": "text_classification",
            "label_id": schema["labels"][0]["id"],
        }, {}

    monkeypatch.setattr(inference, "predict", predict)
    tasks.execute_job.run(job_id)
    result = client.get("/api/v1/jobs/" + job_id, headers=headers).json()
    assert result["status"] == "cancelled" and result["counts"]["cancelled"] == 1
    assert not client.get("/api/v1/items/" + item["id"], headers=headers).json()[
        "revisions"
    ]


def test_worker_rechecks_disabled_actor_and_all_failed_status(
    client, accounts, workspace, monkeypatch
):
    project, _, _, headers, item = text_item(client, workspace)
    with session_scope() as db:
        job = make_job(db, accounts, project)
        job_id = job.id
        db.get(User, accounts["ids"]["owner"]).enabled = False
    tasks.execute_job.run(job_id)
    with session_scope() as db:
        assert db.get(Job, job_id).status == "failed"


def test_recovery_fences_expired_worker_and_does_not_duplicate_events(
    client, accounts, workspace
):
    project, _, _, _, _ = text_item(client, workspace)
    with session_scope() as db:
        job = make_job(db, accounts, project)
        job_id = job.id
        for event in db.scalars(select(Outbox).where(Outbox.topic == "job")).all():
            event.delivered = True
        job.status = "running"
        job.lease_token = "old"
        job.lease_until = now() - timedelta(seconds=1)
    tasks.recover_once()
    tasks.recover_once()
    with session_scope() as db:
        job = db.get(Job, job_id)
        assert job.status == "queued" and job.lease_token is None
        events = db.scalars(
            select(Outbox).where(Outbox.topic == "job", Outbox.delivered == False)
        ).all()
        assert len(events) == 1
        with pytest.raises(LeaseLost):
            check_active(db, job, "old")
        job.status = "cancel_requested"
        db.flush()
        with pytest.raises(JobCancelled):
            check_active(db, job)


def test_outbox_delivery_broker_failure_preserves_event_and_mongo_upsert(
    env, monkeypatch
):
    import pymongo

    seen = []

    class Collection:
        def create_index(self, *a, **kw):
            pass

        def update_one(self, *a, **kw):
            seen.append(a[0]["event_id"])

    class Mongo:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def __getitem__(self, key):
            return SimpleNamespace(events=Collection())

    monkeypatch.setattr(pymongo, "MongoClient", lambda *a, **kw: Mongo())
    with session_scope() as db:
        db.add(Outbox(topic="audit", payload={"action": "test"}))
    assert tasks.dispatch_once() == 1 and len(seen) == 1
    assert tasks.dispatch_once() == 0


def test_system_status_reports_failures_without_losing_postgres(client, accounts):
    response = client.get("/api/v1/admin/system", headers=accounts["admin"])
    assert response.status_code == 200
    status = response.json()
    assert (
        status["postgresql"] is True
        and status["mongodb"] is False
        and status["redis_cache"] is False
    )
    assert (
        client.get("/api/v1/admin/events", headers=accounts["admin"]).status_code == 503
    )


def test_cli_configure_and_seed_are_idempotent(env, tmp_path, monkeypatch):
    from app import cli

    root = tmp_path / "backend"
    root.mkdir()
    monkeypatch.setattr(cli, "ROOT", root)
    monkeypatch.setattr("sys.argv", ["cli", "configure"])
    cli.main()
    key = (root / ".env").read_text()
    cli.main()
    assert (root / ".env").read_text() == key
    assert (root / ".env").stat().st_mode & 0o777 == 0o600
    monkeypatch.setattr("sys.argv", ["cli", "seed-demo"])
    cli.main()
    cli.main()
    with session_scope() as db:
        assert len(db.scalars(select(User)).all()) == 1


def test_restore_refuses_main_database_before_connect(tmp_path):
    import subprocess, sys
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "scripts" / "restore.py"
    response = subprocess.run(
        [
            sys.executable,
            str(script),
            str(tmp_path),
            "--database",
            "annotation_db",
            "--objects",
            str(tmp_path / "new"),
        ],
        capture_output=True,
        text=True,
    )
    assert response.returncode != 0 and "annotation_restore_" in response.stderr
