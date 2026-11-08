"""Regressions found during live, whole-system acceptance."""

from types import SimpleNamespace
from uuid import uuid4
import httpx
import pytest
from sqlalchemy import select, func
from fastapi import HTTPException
from app import inference, tasks
from app.db import session_scope
from app.models import ModelSpec, Deployment, Project, LabelSchema, Item, Job, Revision


@pytest.mark.parametrize("state", ["failed", "stopped", "candidate"])
def test_unavailable_model_cannot_fall_back_to_endpoint_or_cache(
    client, workspace, monkeypatch, state
):
    project, schema, dataset, headers = workspace()
    item = client.post(
        f"/api/v1/projects/{project['id']}/texts",
        headers=headers,
        json={"dataset_id": dataset["id"], "text": "fixture"},
    ).json()
    client.post(
        f"/api/v1/datasets/{dataset['id']}/publish", headers=headers
    ).raise_for_status()
    with session_scope() as db:
        model = ModelSpec(
            name="fixture",
            adapter="http",
            task_type=project["task_type"],
            config={
                "endpoint": "http://127.0.0.1:8101",
                "candidate": state == "candidate",
            },
        )
        db.add(model)
        db.flush()
        mid = model.id
        if state != "candidate":
            db.add(Deployment(model_id=mid, status=state, config={"port": 8101}))
        db.flush()

        def touched(*args, **kwargs):
            raise AssertionError(
                "Unavailable models must not read cache or send requests"
            )

        monkeypatch.setattr(inference.Redis, "from_url", touched)
        monkeypatch.setattr(inference.httpx, "post", touched)
        with pytest.raises(HTTPException) as error:
            inference.predict(
                db,
                model,
                SimpleNamespace(project_id=project["id"]),
                db.get(Project, project["id"]),
                db.get(LabelSchema, schema["id"]),
                db.get(Item, item["id"]),
            )
        assert error.value.detail["code"] == "MODEL_UNAVAILABLE"
    assert (
        client.get(f"/api/v1/projects/{project['id']}/models", headers=headers).json()
        == []
    )
    for route in ("prediction-jobs", "evaluations"):
        response = client.post(
            f"/api/v1/projects/{project['id']}/{route}",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={
                "dataset_id": dataset["id"],
                "schema_id": schema["id"],
                "model_id": mid,
            },
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "MODEL_UNAVAILABLE"
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0


def test_http_image_plugin_retains_scores_supplied_in_signals(
    client, workspace, monkeypatch
):
    from test_workflow import image_bytes

    project, schema, _, headers = workspace("image_detection")
    job = client.post(
        f"/api/v1/projects/{project['id']}/imports",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        files={"file": ("fixture.png", image_bytes())},
    ).json()
    tasks.execute_job.run(job["id"])
    item = client.get(
        f"/api/v1/projects/{project['id']}/items", headers=headers
    ).json()[0]

    def response(url, **kwargs):
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "content": {
                    "task_type": "image_detection",
                    "coordinate_space": "original_image_pixels",
                    "explicit_empty": False,
                    "boxes": [
                        {"label_id": schema["labels"][0]["id"], "xyxy": [1, 2, 60, 70]}
                    ],
                },
                "signals": {"box_scores": [0.4]},
            },
        )

    monkeypatch.setattr(inference.httpx, "post", response)
    with session_scope() as db:
        model = ModelSpec(
            name="fixture",
            adapter="http",
            task_type="image_detection",
            config={"endpoint": "http://127.0.0.1:8101"},
        )
        db.add(model)
        db.flush()
        mid = model.id
    imported = client.get("/api/v1/jobs/" + job["id"], headers=headers).json()
    prediction = client.post(
        f"/api/v1/projects/{project['id']}/prediction-jobs",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={
            "dataset_id": imported["result"]["dataset_id"],
            "schema_id": schema["id"],
            "model_id": mid,
        },
    ).json()
    tasks.execute_job.run(prediction["id"])
    with session_scope() as db:
        revision = db.scalar(
            select(Revision).where(
                Revision.item_id == item["id"], Revision.source == "model"
            )
        )
        assert revision.signals["box_scores"] == [0.4]
        assert "low_model_score" in revision.signals["review_reasons"]
