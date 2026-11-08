"""Supplement the live fixture with eval, image Agent and model readiness checks.

Run while runtime_acceptance.py --serve is running, from backend.
"""

import io
import json
from pathlib import Path
import time
from uuid import uuid4
import zipfile
import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
folder = ROOT / ".local/runtime-acceptance"
fixture = json.loads((folder / "report.json").read_text())
assert fixture["fixture"] and fixture["status"] == "passed"
results = {}

with httpx.Client(base_url="http://127.0.0.1:8000/api/v1", timeout=30) as client:
    login = client.post(
        "/auth/login",
        json={
            "username": "admin",
            "password": (ROOT / ".local/admin-password").read_text().strip(),
        },
    )
    login.raise_for_status()
    client.headers["Authorization"] = "Bearer " + login.json()["access_token"]

    def api(method, path, **kwargs):
        response = client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    def job(path, body=None, **kwargs):
        return api(
            "POST", path, headers={"Idempotency-Key": str(uuid4())}, json=body, **kwargs
        )

    def wait(path, status="succeeded"):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            value = api("GET", path)
            if value["status"] in (
                "succeeded",
                "failed",
                "partially_failed",
                "cancelled",
                "completed",
                "needs_input",
            ):
                assert value["status"] == status, value
                return value
            time.sleep(0.2)
        raise AssertionError("timeout " + path)

    text = fixture["projects"]["text_classification"]
    image = fixture["projects"]["image_detection"]
    for data in (text, image):
        pid, sid = data["id"], data["schema_id"]
        schema = next(
            x for x in api("GET", f"/projects/{pid}/schemas") if x["id"] == sid
        )
        if data is text:
            payload = json.dumps(
                {
                    "text": "Independent evaluation fixture",
                    "label_id": schema["labels"][0]["id"],
                }
            ).encode()
            filename = "reference.jsonl"
        else:
            binary = io.BytesIO()
            Image.new("RGB", (100, 80), "gray").save(binary, "PNG")
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("images/ref.png", binary.getvalue())
                z.writestr(
                    "annotations.json",
                    json.dumps(
                        {
                            "images": [
                                {
                                    "id": 1,
                                    "file_name": "images/ref.png",
                                    "width": 100,
                                    "height": 80,
                                }
                            ],
                            "categories": [{"id": 0, "name": "A"}],
                            "annotations": [
                                {
                                    "image_id": 1,
                                    "category_id": 0,
                                    "bbox": [1, 2, 59, 68],
                                }
                            ],
                        }
                    ),
                )
            payload, filename = archive.getvalue(), "reference.zip"
        imported = api(
            "POST",
            f"/projects/{pid}/imports",
            headers={"Idempotency-Key": str(uuid4())},
            files={"file": (filename, payload)},
            data={"labels_purpose": "evaluation", "schema_id": sid, "split": "test"},
        )
        finished = wait("/jobs/" + imported["id"])
        dataset = finished["result"]["dataset_id"]
        items = api("GET", f"/projects/{pid}/items", params={"dataset_id": dataset})
        assert len(items) == 1
        detail = api("GET", "/items/" + items[0]["id"])
        assert "reference" not in detail and not detail["revisions"]
        evaluation = job(
            f"/projects/{pid}/evaluations",
            {"dataset_id": dataset, "schema_id": sid, "model_id": data["model_id"]},
        )
        evaluated = wait("/jobs/" + evaluation["id"])
        metrics = evaluated["result"]
        if data is text:
            assert metrics["accuracy"] == 1 and metrics["count"] == 1
        else:
            assert metrics["map_50"] == 1 and metrics["map_50_95"] == 1
            image_dataset = dataset
        results[data["id"]] = {"evaluation": metrics, "reference_hidden": True}
    configs = api("GET", "/admin/agent-configs")
    config = next(c for c in configs if c["id"] == fixture["agent_config_id"])
    api(
        "POST",
        "/admin/providers/" + config["provider_id"] + "/grant",
        params={"project_id": image["id"]},
    )
    run = job(
        f"/projects/{image['id']}/agent-runs",
        {
            "config_id": config["id"],
            "instruction": "给所选版本一张图片预标注并汇总低分框",
            "dataset_id": image_dataset,
            "model_id": image["model_id"],
            "max_items": 1,
        },
    )
    run = wait("/agent-runs/" + run["id"], "completed")
    assert run["summary"]["predicted"] == 1
    assert run["summary"]["review_reasons"]["low_model_score"] == 1
    results["image_agent"] = {"run_id": run["id"], "summary": run["summary"]}
    deployments = api("GET", "/admin/deployments")
    failed = next(
        d
        for d in deployments
        if d["status"] == "failed" and d["config"]["model_id"] == text["model_id"]
    )
    available = api("GET", f"/projects/{text['id']}/models")
    assert failed["model_id"] not in {m["id"] for m in available}
    response = client.post(
        f"/projects/{text['id']}/prediction-jobs",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "dataset_id": text["dataset_id"],
            "schema_id": text["schema_id"],
            "model_id": failed["model_id"],
        },
    )
    assert (
        response.status_code == 400
        and response.json()["error"]["code"] == "MODEL_UNAVAILABLE"
    )
    results["failed_deployment_blocked"] = True
    # Browser actions already saved these two synthetic examples.
    submitted_items = [
        item
        for item in api("GET", f"/projects/{text['id']}/items")
        if item["work"]["state"] == "submitted"
    ]
    assert submitted_items, "Complete a text submission in the browser first"
    detail = api("GET", "/items/" + image["item_id"])
    revision = next(
        r for r in detail["revisions"] if r["id"] == detail["work"]["draft_id"]
    )
    assert len(revision["content"]["boxes"]) == 2
    for box in revision["content"]["boxes"]:
        x1, y1, x2, y2 = box["xyxy"]
        assert 0 <= x1 < x2 <= 100 and 0 <= y1 < y2 <= 80
    results["browser_saved_original_coordinates"] = revision["content"]["boxes"]
    results["status"] = "passed"
    (folder / "additional-live.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2)
    )
    print(json.dumps(results, ensure_ascii=False))
