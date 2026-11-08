import io
import json
import zipfile
from uuid import uuid4
from PIL import Image


def test_auth_refresh_reuse_and_permissions(client, accounts, workspace):
    project, schema, dataset, headers = workspace()
    assert client.get("/api/v1/projects").status_code == 401
    assert (
        client.get(
            f"/api/v1/projects/{project['id']}", headers=accounts["headers"]["outsider"]
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/projects",
            headers=headers,
            json={"name": "   ", "task_type": "text_classification"},
        ).status_code
        == 422
    )
    token = client.cookies.get("refresh_token")
    assert client.post("/api/v1/auth/refresh").status_code == 200
    assert (
        client.post(
            "/api/v1/auth/refresh", headers={"Cookie": "refresh_token=" + token}
        ).status_code
        == 401
    )
    assert client.post("/api/v1/auth/refresh").status_code == 401


def save(client, item, schema, headers, version=1, content=None, operation=None):
    body = {
        "schema_id": schema["id"],
        "content": content
        or {"task_type": "text_classification", "label_id": schema["labels"][0]["id"]},
        "expected_version": version,
        "operation_id": operation or str(uuid4()),
    }
    response = client.post(
        f"/api/v1/items/{item['id']}/revisions", headers=headers, json=body
    )
    return response, body


def test_text_revision_review_export_and_concurrency(client, accounts, workspace):
    project, schema, dataset, headers = workspace()
    response = client.post(
        f"/api/v1/projects/{project['id']}/texts",
        headers=headers,
        json={"dataset_id": dataset["id"], "text": "测试文本"},
    )
    assert response.status_code == 200, response.text
    item = response.json()
    response, body = save(client, item, schema, headers)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert (
        client.post(
            f"/api/v1/items/{item['id']}/revisions", headers=headers, json=body
        ).json()["revision"]["id"]
        == saved["revision"]["id"]
    )
    assert save(client, item, schema, headers)[0].status_code == 409
    submitted = client.post(
        f"/api/v1/items/{item['id']}/submit",
        headers=headers,
        json={
            "schema_id": schema["id"],
            "revision_id": saved["revision"]["id"],
            "expected_version": saved["work"]["version"],
        },
    )
    assert submitted.status_code == 200, submitted.text
    review = {
        "decision": "accept",
        "expected_version": submitted.json()["version"],
        "operation_id": str(uuid4()),
    }
    assert (
        client.post(
            f"/api/v1/revisions/{saved['revision']['id']}/reviews",
            headers=headers,
            json=review,
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/projects/{project['id']}/members?user_id={accounts['ids']['reviewer']}&role=reviewer",
            headers=headers,
        ).status_code
        == 200
    )
    accepted = client.post(
        f"/api/v1/revisions/{saved['revision']['id']}/reviews",
        headers=accounts["headers"]["reviewer"],
        json=review,
    )
    assert accepted.status_code == 200, accepted.text
    assert (
        client.post(
            f"/api/v1/revisions/{saved['revision']['id']}/reviews",
            headers=accounts["headers"]["reviewer"],
            json=review,
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/datasets/{dataset['id']}/publish", headers=headers
        ).status_code
        == 200
    )
    response = client.post(
        f"/api/v1/projects/{project['id']}/exports",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={
            "dataset_id": dataset["id"],
            "schema_id": schema["id"],
            "format": "jsonl",
        },
    )
    assert response.status_code == 202, response.text
    job = response.json()
    # 后续修改导致当前审核失效，但已固定导出快照仍引用之前版本。
    current = client.get(f"/api/v1/items/{item['id']}", headers=headers).json()
    edited, _ = save(client, item, schema, headers, current["work"]["version"])
    assert edited.status_code == 200, edited.text
    assert edited.json()["work"]["accepted_id"] is None
    from app.tasks import execute_job

    execute_job.run(job["id"])
    finished = client.get(f"/api/v1/jobs/{job['id']}", headers=headers).json()
    assert finished["status"] == "succeeded", finished
    response = client.get(
        f"/api/v1/exports/{finished['result']['export_id']}/download", headers=headers
    )
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    rows = archive.read("annotations.jsonl").decode().splitlines()
    assert json.loads(rows[0])["revision_id"] == saved["revision"]["id"]
    assert (
        client.get(
            f"/api/v1/exports/{finished['result']['export_id']}/download",
            headers=accounts["headers"]["outsider"],
        ).status_code
        == 404
    )


def image_bytes():
    output = io.BytesIO()
    Image.new("RGB", (100, 80), "white").save(output, format="PNG")
    return output.getvalue()


def test_image_import_validation_and_yolo_roundtrip(client, accounts, workspace):
    project, schema, dataset, headers = workspace("image_detection")
    response = client.post(
        f"/api/v1/projects/{project['id']}/imports",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        files={"file": ("sample.png", image_bytes(), "image/png")},
    )
    assert response.status_code == 202, response.text
    from app.tasks import execute_job

    execute_job.run(response.json()["id"])
    item = client.get(
        f"/api/v1/projects/{project['id']}/items", headers=headers
    ).json()[0]
    content = {
        "task_type": "image_detection",
        "coordinate_space": "original_image_pixels",
        "explicit_empty": False,
        "boxes": [
            {
                "object_id": str(uuid4()),
                "label_id": schema["labels"][0]["id"],
                "xyxy": [1, 2, 60, 70],
            }
        ],
    }
    invalid = {**content, "boxes": [{**content["boxes"][0], "xyxy": [-1, 0, 20, 20]}]}
    assert save(client, item, schema, headers, content=invalid)[0].status_code == 400
    response, _ = save(client, item, schema, headers, content=content)
    assert response.status_code == 200, response.text
    saved = response.json()
    submitted = client.post(
        f"/api/v1/items/{item['id']}/submit",
        headers=headers,
        json={
            "schema_id": schema["id"],
            "revision_id": saved["revision"]["id"],
            "expected_version": saved["work"]["version"],
        },
    ).json()
    assert (
        client.post(
            f"/api/v1/revisions/{saved['revision']['id']}/reviews",
            headers=accounts["admin"],
            json={
                "decision": "accept",
                "expected_version": submitted["version"],
                "operation_id": str(uuid4()),
            },
        ).status_code
        == 200
    )
    dataset_id = client.get(
        f"/api/v1/projects/{project['id']}/datasets", headers=headers
    ).json()[0]["id"]
    response = client.post(
        f"/api/v1/projects/{project['id']}/exports",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={"dataset_id": dataset_id, "schema_id": schema["id"], "format": "yolo"},
    )
    assert response.status_code == 202, response.text
    execute_job.run(response.json()["id"])
    job = client.get(f"/api/v1/jobs/{response.json()['id']}", headers=headers).json()
    assert job["status"] == "succeeded", job
    binary = client.get(
        f"/api/v1/exports/{job['result']['export_id']}/download", headers=headers
    ).content
    response = client.post(
        f"/api/v1/projects/{project['id']}/imports",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        files={"file": ("roundtrip.zip", binary, "application/zip")},
        data={"labels_purpose": "seed", "schema_id": schema["id"]},
    )
    assert response.status_code == 202, response.text
    execute_job.run(response.json()["id"])
    imported_job = client.get(
        f"/api/v1/jobs/{response.json()['id']}", headers=headers
    ).json()
    assert imported_job["status"] == "succeeded", imported_job
    revisions = client.get(f"/api/v1/items/{item['id']}", headers=headers).json()[
        "revisions"
    ]
    imported = next(x for x in revisions if x["source"] == "external")
    assert (
        max(
            abs(a - b)
            for a, b in zip(
                imported["content"]["boxes"][0]["xyxy"], content["boxes"][0]["xyxy"]
            )
        )
        < 0.5
    )


def test_zip_path_traversal_is_rejected(client, workspace):
    project, schema, dataset, headers = workspace("image_detection")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("../outside.png", image_bytes())
    response = client.post(
        f"/api/v1/projects/{project['id']}/imports",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        files={"file": ("unsafe.zip", archive.getvalue())},
    )
    from app.tasks import execute_job

    execute_job.run(response.json()["id"])
    assert (
        client.get(f"/api/v1/jobs/{response.json()['id']}", headers=headers).json()[
            "status"
        ]
        == "failed"
    )


def test_reference_labels_are_not_exposed(client, workspace):
    project, schema, dataset, headers = workspace()
    from app.tasks import execute_job

    payload = json.dumps(
        {"text": "参考文本", "label_id": schema["labels"][0]["id"]}
    ).encode()
    response = client.post(
        f"/api/v1/projects/{project['id']}/imports",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        files={"file": ("data.jsonl", payload)},
        data={"labels_purpose": "evaluation", "schema_id": schema["id"]},
    )
    execute_job.run(response.json()["id"])
    item = client.get(
        f"/api/v1/projects/{project['id']}/items", headers=headers
    ).json()[0]
    detail = client.get(f"/api/v1/items/{item['id']}", headers=headers).json()
    assert not detail["revisions"]
    assert "reference" not in detail
