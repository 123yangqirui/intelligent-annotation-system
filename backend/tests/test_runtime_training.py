"""Deployment/training contracts use controlled substitutes; no GPU quality claims."""

import io, json, subprocess, sys
from types import SimpleNamespace
from uuid import uuid4
from pathlib import Path
import pytest
from app.db import session_scope
from app.models import (
    ModelSpec,
    Deployment,
    Job,
    Project,
    Item,
    Revision,
    LabelSchema,
    Reference,
)
from app import runtime, training
from app.security import fail
from test_maturity_regressions import text_item
from sqlalchemy import select


@pytest.mark.parametrize(
    "checkpoint", ["../../private", "..", "-bad", "org/../name", "file:///tmp/model"]
)
def test_checkpoint_validation(checkpoint, env):
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        runtime.validate_deployment(
            {"template": "bert", "checkpoint": checkpoint, "port": 8101},
            SimpleNamespace(task_type="text_classification", adapter="bert", config={}),
        )


@pytest.mark.parametrize(
    "template,task,adapter,checkpoint",
    [
        ("bert", "text_classification", "bert", "org/model"),
        ("yolo", "image_detection", "yolo", "yolo11n.pt"),
        ("qwen", "text_classification", "llm", "Qwen/Qwen3-0.6B"),
        ("deepseek", "text_classification", "llm", "deepseek/model"),
    ],
)
def test_deployment_commands_and_health(
    env, monkeypatch, template, task, adapter, checkpoint
):
    commands = []

    def command(args, **kw):
        commands.append(args)
        return SimpleNamespace(
            returncode=1 if args[:2] == ["docker", "inspect"] else 0, stdout="false"
        )

    monkeypatch.setattr(runtime.shutil, "which", lambda _: "/test/docker")
    monkeypatch.setattr(runtime.subprocess, "run", command)
    monkeypatch.setattr(
        runtime.httpx, "get", lambda *a, **kw: SimpleNamespace(is_success=True)
    )
    with session_scope() as db:
        model = ModelSpec(name="test", adapter=adapter, task_type=task, config={})
        db.add(model)
        db.flush()
        deployment = Deployment(
            model_id=model.id,
            config={"template": template, "checkpoint": checkpoint, "port": 8102},
        )
        db.add(deployment)
        db.flush()
        result = runtime.run_deployment(
            db, SimpleNamespace(type="deploy", config={"deployment_id": deployment.id})
        )
        assert deployment.status == "ready" and result["deployment_id"] == deployment.id
        run = next(c for c in commands if c[:2] == ["docker", "run"])
        assert run[run.index("-p") + 1] == "127.0.0.1:8102:8000"
        assert ("--gpus" in run) == (adapter == "llm")
        stop = runtime.run_deployment(
            db,
            SimpleNamespace(
                type="stop_deployment", config={"deployment_id": deployment.id}
            ),
        )
        assert stop["status"] == "stopped"


def test_missing_docker_is_explicit_and_no_container_started(env, monkeypatch):
    monkeypatch.setattr(runtime.shutil, "which", lambda _: None)
    with session_scope() as db:
        model = ModelSpec(
            name="test", adapter="bert", task_type="text_classification", config={}
        )
        db.add(model)
        db.flush()
        deployment = Deployment(model_id=model.id, config={})
        db.add(deployment)
        db.flush()
        with pytest.raises(RuntimeError, match="Docker"):
            runtime.run_deployment(
                db,
                SimpleNamespace(type="deploy", config={"deployment_id": deployment.id}),
            )
        assert deployment.status == "failed"


@pytest.mark.parametrize(
    "config",
    [
        {"checkpoint": "org/model", "epochs": 0},
        {"checkpoint": "org/model", "epochs": 2.5},
        {"checkpoint": "org/model", "learning_rate": float("nan")},
        {"checkpoint": "org/model", "shell": "bad"},
        {"checkpoint": ".."},
    ],
)
def test_training_rejects_invalid_config(config):
    with pytest.raises(ValueError):
        training.validate_training_config(config)


def test_training_snapshot_excludes_test_and_registers_candidate(
    client, workspace, monkeypatch
):
    project, schema, dataset, headers = workspace()
    snapshot = []
    from test_workflow import save

    for split in ("train", "val", "test"):
        item = client.post(
            "/api/v1/projects/" + project["id"] + "/texts",
            headers=headers,
            json={"dataset_id": dataset["id"], "text": split + " example"},
        ).json()
        revision = save(client, item, schema, headers)[0].json()["revision"]
        snapshot.append(
            {"item_id": item["id"], "revision_id": revision["id"], "split": split}
        )
    monkeypatch.setattr(
        training.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0)
    )
    captured = []
    checks = []

    def train(command, log, check):
        check()
        request = json.loads(Path(command[-1]).read_text())
        captured.append(request)
        Path(request["output"]).mkdir(parents=True)

    monkeypatch.setattr(training, "run_training", train)
    with session_scope() as db:
        job = SimpleNamespace(
            id=str(uuid4()),
            type="training",
            config={
                "schema_id": schema["id"],
                "snapshot": snapshot,
                "config": {"checkpoint": "org/model", "epochs": 1},
            },
        )
        result = training.run_experiment(
            db, job, db.get(Project, project["id"]), check=lambda: checks.append(True)
        )
        model = db.get(ModelSpec, result["model_id"])
        assert model.config["candidate"] is True and result["status"] == "candidate"
    assert {x["split"] for x in captured[0]["rows"]} == {"train", "val"}
    assert checks


def test_evaluation_reads_reference_only_in_evaluator(client, workspace, monkeypatch):
    from app import inference

    project, schema, dataset, _, item = text_item(client, workspace)
    with session_scope() as db:
        model = ModelSpec(
            name="fixture", adapter="http", task_type="text_classification", config={}
        )
        db.add(model)
        db.flush()
        db.add(
            Reference(
                item_id=item["id"],
                schema_id=schema["id"],
                content={
                    "task_type": "text_classification",
                    "label_id": schema["labels"][0]["id"],
                },
            )
        )
        db.flush()
        captures = []

        def predict(db, model, job, project, schema, item):
            captures.append(item)
            assert not hasattr(item, "reference")
            return {"label_id": schema.labels[0]["id"]}, {}

        monkeypatch.setattr(inference, "predict", predict)
        job = SimpleNamespace(
            type="evaluation",
            config={
                "model_id": model.id,
                "schema_id": schema["id"],
                "dataset_id": dataset["id"],
                "item_ids": [item["id"]],
            },
        )
        result = training.run_experiment(db, job, db.get(Project, project["id"]))
        assert result["accuracy"] == 1 and result["count"] == 1 and len(captures) == 1


def test_training_process_cancels_and_success_writes_log(tmp_path):
    from app.job_control import JobCancelled

    with (tmp_path / "log").open("w") as log:
        training.run_training(
            [sys.executable, "-c", 'print("done")'], log, lambda: None
        )
    assert (tmp_path / "log").read_text().strip() == "done"
    with (tmp_path / "cancel-log").open("w") as log:

        def cancel():
            raise JobCancelled("cancel")

        with pytest.raises(JobCancelled):
            training.run_training(
                [sys.executable, "-c", "import time; time.sleep(30)"], log, cancel
            )


def test_ap_requires_valid_scores_and_counts_duplicate_predictions():
    truth = [{"label_id": "A", "xyxy": [0, 0, 10, 10]}]
    assert training.detection_counts(truth, truth + truth) == (1, 1, 0)
    assert (
        training.detection_map([(truth, truth, [float("nan")])], ["A"])["map_50"]
        is None
    )
    with pytest.raises(ValueError):
        training.classification_metrics(["A"], [], ["A"])
