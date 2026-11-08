"""Regression cases discovered while comparing established annotation workflows."""

import json
from uuid import uuid4
import pytest
from sqlalchemy import select
from app.db import session_scope
from app.models import Work, ModelSpec, Provider, ProviderGrant, Job, Revision
from app.security import crypt
from test_workflow import save


def text_item(client, workspace):
    project, schema, dataset, headers = workspace()
    item = client.post(
        f"/api/v1/projects/{project['id']}/texts",
        headers=headers,
        json={"dataset_id": dataset["id"], "text": "需要售后帮助"},
    ).json()
    return project, schema, dataset, headers, item


def test_claim_blocks_other_editor_without_changing_annotation_version(
    client, accounts, workspace
):
    project, schema, _, headers, item = text_item(client, workspace)
    client.post(
        f"/api/v1/projects/{project['id']}/members",
        headers=headers,
        params={"user_id": accounts["ids"]["outsider"], "role": "annotator"},
    )
    response = client.post(
        f"/api/v1/items/{item['id']}/claim",
        headers=headers,
        params={"schema_id": schema["id"]},
    )
    assert response.status_code == 200
    assert response.json()["version"] == 1
    blocked, _ = save(client, item, schema, accounts["headers"]["outsider"])
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "CLAIMED"
    assert save(client, item, schema, headers)[0].status_code == 200


def test_foreign_schema_cannot_create_work_state(client, workspace):
    _, _, _, headers, item = text_item(client, workspace)
    _, foreign, _, _ = workspace()
    response = client.post(
        f"/api/v1/items/{item['id']}/submit",
        headers=headers,
        json={
            "schema_id": foreign["id"],
            "revision_id": str(uuid4()),
            "expected_version": 1,
        },
    )
    assert response.status_code == 400
    response = client.delete(
        f"/api/v1/items/{item['id']}/claim",
        headers=headers,
        params={"schema_id": foreign["id"]},
    )
    assert response.status_code == 400
    with session_scope() as db:
        assert (
            db.scalar(
                select(Work).where(
                    Work.item_id == item["id"], Work.schema_id == foreign["id"]
                )
            )
            is None
        )


def test_cached_prediction_still_checks_provider_grant(
    client, accounts, workspace, monkeypatch
):
    from app import inference
    from app.models import Project, LabelSchema, Item, User

    project, schema, _, _, item = text_item(client, workspace)

    class Cached:
        def get(self, key):
            return json.dumps(
                {
                    "content": {
                        "task_type": "text_classification",
                        "label_id": schema["labels"][0]["id"],
                    }
                }
            )

    monkeypatch.setattr(inference.Redis, "from_url", lambda *a, **kw: Cached())
    with session_scope() as db:
        provider = Provider(
            name="service",
            base_url="http://127.0.0.1:8101",
            model_name="test",
            encrypted_key=crypt().encrypt(b"key").decode(),
        )
        db.add(provider)
        db.flush()
        model = ModelSpec(
            name="LLM",
            adapter="llm",
            task_type="text_classification",
            provider_id=provider.id,
            config={},
        )
        db.add(model)
        db.flush()
        job = Job(
            actor_id=accounts["ids"]["owner"],
            project_id=project["id"],
            type="prediction",
            config={},
            idempotency_key=str(uuid4()),
            request_hash="x",
        )
        db.add(job)
        db.flush()
        with pytest.raises(Exception) as error:
            inference.predict(
                db,
                model,
                job,
                db.get(Project, project["id"]),
                db.get(LabelSchema, schema["id"]),
                db.get(Item, item["id"]),
            )
        assert getattr(error.value, "status_code", None) == 403


@pytest.mark.parametrize(
    "content",
    [
        {
            "task_type": "image_detection",
            "coordinate_space": "original_image_pixels",
            "boxes": [None],
        },
        {
            "task_type": "image_detection",
            "coordinate_space": "original_image_pixels",
            "boxes": [{"xyxy": None}],
        },
        {
            "task_type": "image_detection",
            "coordinate_space": "original_image_pixels",
            "boxes": [{"xyxy": [0, 0, 1, 1], "object_id": []}],
        },
    ],
)
def test_malformed_boxes_return_validation_error(content, client, workspace):
    from app.imports import add_item
    from app.models import Project, Dataset
    from test_workflow import image_bytes

    project, schema, dataset, headers = workspace("image_detection")
    with session_scope() as db:
        item = add_item(
            db,
            db.get(Project, project["id"]),
            db.get(Dataset, dataset["id"]),
            image_bytes(),
        )
        db.flush()
        item_id = item.id
    response, _ = save(client, {"id": item_id}, schema, headers, content=content)
    assert response.status_code == 400


def test_evaluation_rejects_incompatible_model_before_queueing(
    client, accounts, workspace
):
    project, schema, dataset, headers, _ = text_item(client, workspace)
    client.post(f"/api/v1/datasets/{dataset['id']}/publish", headers=headers)
    model = client.post(
        "/api/v1/admin/models",
        headers=accounts["admin"],
        json={
            "name": "wrong task",
            "adapter": "yolo",
            "task_type": "image_detection",
            "config": {"endpoint": "http://127.0.0.1:8101"},
        },
    ).json()
    response = client.post(
        f"/api/v1/projects/{project['id']}/evaluations",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={
            "dataset_id": dataset["id"],
            "schema_id": schema["id"],
            "model_id": model["id"],
        },
    )
    assert response.status_code == 400


def test_review_feedback_visible_to_annotator(client, accounts, workspace):
    project, schema, _, headers, item = text_item(client, workspace)
    saved = save(client, item, schema, headers)[0].json()
    submitted = client.post(
        f"/api/v1/items/{item['id']}/submit",
        headers=headers,
        json={
            "schema_id": schema["id"],
            "revision_id": saved["revision"]["id"],
            "expected_version": saved["work"]["version"],
        },
    ).json()
    response = client.post(
        f"/api/v1/revisions/{saved['revision']['id']}/reviews",
        headers=accounts["admin"],
        json={
            "decision": "return",
            "comment": "请根据售后规则修改类别",
            "operation_id": str(uuid4()),
            "expected_version": submitted["version"],
        },
    )
    assert response.status_code == 200
    data = client.get(f"/api/v1/items/{item['id']}", headers=headers).json()
    assert data["reviews"][0]["comment"] == "请根据售后规则修改类别"
    assert data["reviews"][0]["reviewer_name"] == "admin"
