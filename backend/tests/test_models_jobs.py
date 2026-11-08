import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from uuid import uuid4
from app.db import new_session


@contextlib.contextmanager
def model_server():
    captures = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            captures.append(body)
            data = json.loads(body["messages"][1]["content"])
            label = data["labels"][0]["id"]
            response = {
                "choices": [{"message": {"content": json.dumps({"label_id": label})}}],
                "usage": {"total_tokens": 25},
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, captures
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def prepare(client, accounts, workspace, port, budget=100000):
    project, schema, dataset, headers = workspace()
    item = client.post(
        f"/api/v1/projects/{project['id']}/texts",
        headers=headers,
        json={"dataset_id": dataset["id"], "text": "不应被修改的文本"},
    ).json()
    client.post(f"/api/v1/datasets/{dataset['id']}/publish", headers=headers)
    provider = client.post(
        "/api/v1/admin/providers",
        headers=accounts["admin"],
        json={
            "name": "test service",
            "base_url": f"http://127.0.0.1:{port}/v1",
            "model_name": "fixture",
            "api_key": "test-only-key",
            "budget_tokens": budget,
        },
    )
    assert provider.status_code == 201, provider.text
    assert "encrypted_key" not in provider.json()
    provider = provider.json()
    assert (
        client.post(
            f"/api/v1/admin/providers/{provider['id']}/grant?project_id={project['id']}",
            headers=accounts["admin"],
        ).status_code
        == 200
    )
    model = client.post(
        "/api/v1/admin/models",
        headers=accounts["admin"],
        json={
            "name": "fixture model",
            "adapter": "llm",
            "task_type": "text_classification",
            "provider_id": provider["id"],
        },
    ).json()
    return project, schema, dataset, headers, item, provider, model


def test_prediction_preserves_human_revision_and_is_idempotent(
    client, accounts, workspace
):
    with model_server() as (port, captures):
        project, schema, dataset, headers, item, provider, model = prepare(
            client, accounts, workspace, port
        )
        saved = client.post(
            f"/api/v1/items/{item['id']}/revisions",
            headers=headers,
            json={
                "schema_id": schema["id"],
                "expected_version": 1,
                "operation_id": str(uuid4()),
                "content": {
                    "task_type": "text_classification",
                    "label_id": schema["labels"][1]["id"],
                },
            },
        ).json()
        body = {
            "dataset_id": dataset["id"],
            "schema_id": schema["id"],
            "model_id": model["id"],
        }
        key = str(uuid4())
        response = client.post(
            f"/api/v1/projects/{project['id']}/prediction-jobs",
            headers={**headers, "Idempotency-Key": key},
            json=body,
        )
        assert response.status_code == 202, response.text
        assert (
            client.post(
                f"/api/v1/projects/{project['id']}/prediction-jobs",
                headers={**headers, "Idempotency-Key": key},
                json=body,
            ).json()["id"]
            == response.json()["id"]
        )
        from app.tasks import execute_job

        execute_job.run(response.json()["id"])
        execute_job.run(response.json()["id"])
        result = client.get(
            f"/api/v1/jobs/{response.json()['id']}", headers=headers
        ).json()
        assert result["status"] == "succeeded", result
        detail = client.get(f"/api/v1/items/{item['id']}", headers=headers).json()
        assert detail["work"]["draft_id"] == saved["revision"]["id"]
        assert sum(x["source"] == "model" for x in detail["revisions"]) == 1
        assert len(captures) == 1
        prompt = json.loads(captures[0]["messages"][1]["content"])
        assert "reference" not in prompt and "truth" not in prompt
        providers = client.get(
            "/api/v1/admin/providers", headers=accounts["admin"]
        ).json()
        assert (
            providers[0]["spent_tokens"] == 25 and providers[0]["reserved_tokens"] == 0
        )


def test_budget_exceeded_prevents_network_call(client, accounts, workspace):
    with model_server() as (port, captures):
        project, schema, dataset, headers, item, provider, model = prepare(
            client, accounts, workspace, port, budget=1
        )
        response = client.post(
            f"/api/v1/projects/{project['id']}/prediction-jobs",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={
                "dataset_id": dataset["id"],
                "schema_id": schema["id"],
                "model_id": model["id"],
            },
        )
        from app.tasks import execute_job

        execute_job.run(response.json()["id"])
        result = client.get(
            f"/api/v1/jobs/{response.json()['id']}", headers=headers
        ).json()
        assert result["counts"]["failed"] == 1, result
        assert not captures


def test_queued_cancel_and_recovery(client, accounts, workspace):
    project, schema, dataset, headers = workspace()
    from app.tasks import create_job, execute_job, recover_once
    from app.models import User, Job, Outbox
    from sqlalchemy import select

    with new_session() as db:
        owner = db.get(User, accounts["ids"]["owner"])
        job = create_job(
            db,
            owner,
            project["id"],
            "import",
            {
                "dataset_id": dataset["id"],
                "object_key": "missing",
                "filename": "missing.csv",
            },
            str(uuid4()),
        )
        db.commit()
        job_id = job.id
    assert (
        client.post(f"/api/v1/jobs/{job_id}/cancel", headers=headers).status_code == 200
    )
    execute_job.run(job_id)
    assert (
        client.get(f"/api/v1/jobs/{job_id}", headers=headers).json()["status"]
        == "cancelled"
    )
    recover_once()


def test_deployment_rejects_arbitrary_checkpoint(client, accounts):
    model = client.post(
        "/api/v1/admin/models",
        headers=accounts["admin"],
        json={
            "name": "service",
            "adapter": "http",
            "task_type": "text_classification",
            "config": {"endpoint": "http://127.0.0.1:8101"},
        },
    ).json()
    response = client.post(
        "/api/v1/admin/deployments",
        headers={**accounts["admin"], "Idempotency-Key": str(uuid4())},
        json={
            "model_id": model["id"],
            "template": "bert",
            "port": 8101,
            "checkpoint": "../../private",
        },
    )
    assert response.status_code == 400, response.text
