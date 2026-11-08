import io
from uuid import uuid4


def test_published_schema_and_last_owner_are_protected(client, accounts, workspace):
    project, schema, dataset, headers = workspace()
    assert (
        client.put(
            "/api/v1/schemas/" + schema["id"],
            headers=headers,
            json={"labels": [{"name": "C"}]},
        ).status_code
        == 409
    )
    response = client.post(
        f"/api/v1/projects/{project['id']}/members?user_id={accounts['ids']['owner']}&role=annotator",
        headers=headers,
    )
    assert response.status_code == 400


def test_review_correction_creates_new_accepted_revision(client, accounts, workspace):
    project, schema, dataset, headers = workspace()
    item = client.post(
        f"/api/v1/projects/{project['id']}/texts",
        headers=headers,
        json={"dataset_id": dataset["id"], "text": "待修正文本"},
    ).json()
    content = {
        "task_type": "text_classification",
        "label_id": schema["labels"][0]["id"],
    }
    result = client.post(
        f"/api/v1/items/{item['id']}/revisions",
        headers=headers,
        json={
            "schema_id": schema["id"],
            "expected_version": 1,
            "operation_id": str(uuid4()),
            "content": content,
        },
    ).json()
    submitted = client.post(
        f"/api/v1/items/{item['id']}/submit",
        headers=headers,
        json={
            "schema_id": schema["id"],
            "revision_id": result["revision"]["id"],
            "expected_version": result["work"]["version"],
        },
    ).json()
    client.post(
        f"/api/v1/projects/{project['id']}/members?user_id={accounts['ids']['reviewer']}&role=reviewer",
        headers=headers,
    )
    correction = {**content, "label_id": schema["labels"][1]["id"]}
    response = client.post(
        f"/api/v1/revisions/{result['revision']['id']}/correction",
        headers=accounts["headers"]["reviewer"],
        json={
            "schema_id": schema["id"],
            "expected_version": submitted["version"],
            "operation_id": str(uuid4()),
            "content": correction,
        },
    )
    assert response.status_code == 200, response.text
    detail = client.get(f"/api/v1/items/{item['id']}", headers=headers).json()
    assert detail["work"]["state"] == "accepted"
    assert detail["work"]["accepted_id"] != result["revision"]["id"]
    assert len(detail["revisions"]) == 2


def test_dataset_fork_split_preserves_published_version(client, workspace):
    project, schema, dataset, headers = workspace()
    for i in range(10):
        client.post(
            f"/api/v1/projects/{project['id']}/texts",
            headers=headers,
            json={"dataset_id": dataset["id"], "text": str(i)},
        )
    assert (
        client.post(
            "/api/v1/datasets/" + dataset["id"] + "/publish", headers=headers
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/datasets/" + dataset["id"] + "/split", headers=headers
        ).status_code
        == 400
    )
    fork = client.post(
        "/api/v1/datasets/" + dataset["id"] + "/fork", headers=headers
    ).json()
    result = client.post("/api/v1/datasets/" + fork["id"] + "/split", headers=headers)
    assert result.json() == {"train": 7, "val": 2, "test": 1}
    assert (
        len(
            client.get(
                f"/api/v1/projects/{project['id']}/items?dataset_id={dataset['id']}",
                headers=headers,
            ).json()
        )
        == 10
    )


def test_metric_known_cases():
    from app.training import detection_map, classification_metrics

    truth = [{"label_id": "A", "xyxy": [0, 0, 10, 10]}]
    assert detection_map([(truth, truth, [0.9])], ["A"])["map_50_95"] == 1
    wrong = [{"label_id": "A", "xyxy": [50, 50, 60, 60]}]
    assert detection_map([(truth, wrong, [0.9])], ["A"])["map_50"] == 0
    assert classification_metrics(["A", "B"], ["A", "B"], ["A", "B"])["macro_f1"] == 1
