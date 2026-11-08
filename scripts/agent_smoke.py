"""Local contract fixture, explicitly not a real LLM or trained classifier."""

import argparse, json, threading, time, sys, signal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4
import httpx

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(
        description="通过真实 Worker 验收 Agent 工作流，模型使用本地协议测试服务"
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="验收后保留服务，供浏览器检查；Ctrl+C 会停用测试配置",
    )
    args = parser.parse_args()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            data = b'{"status":"ok","fixture":true}'
            self.send_response(200)
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path.endswith("/chat/completions"):
                payload = json.loads(data["messages"][-1]["content"])
                context = payload["context"]
                selection = context["selection"]
                if "补充" in payload["instruction"] and not payload["additional_input"]:
                    plan = {
                        "goal": "测试补充信息流程",
                        "questions": ["请确认只汇总结果，还是创建新的预标注任务？"],
                    }
                elif payload["additional_input"]:
                    plan = {
                        "goal": "根据补充信息汇总",
                        "steps": [{"action": "summary"}],
                    }
                else:
                    steps = [
                        {
                            "action": "prediction",
                            "dataset_id": selection["dataset_id"],
                            "model_id": selection["model_id"],
                            "limit": min(2, selection["max_items"]),
                        },
                        {"action": "summary"},
                    ]
                    if "export" in context["allowed_actions"]:
                        steps.append(
                            {
                                "action": "export",
                                "dataset_id": selection["dataset_id"],
                                "format": "jsonl",
                            }
                        )
                    plan = {"goal": "测试预标注、汇总与审核快照导出", "steps": steps}
                result = {
                    "choices": [{"message": {"content": json.dumps(plan)}}],
                    "usage": {"total_tokens": 100},
                }
            else:
                result = {
                    "content": {
                        "task_type": "text_classification",
                        "label_id": data["labels"][0]["id"],
                    },
                    "signals": {"score": 0.4, "score_kind": "contract_fixture"},
                }
            binary = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(binary)))
            self.end_headers()
            self.wfile.write(binary)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_port
    report = {}
    with httpx.Client(base_url="http://127.0.0.1:8000/api/v1", timeout=30) as client:

        def request(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            if response.status_code == 401 and path != "/auth/login":
                refreshed = client.post("/auth/refresh")
                refreshed.raise_for_status()
                client.headers["Authorization"] = (
                    "Bearer " + refreshed.json()["access_token"]
                )
                response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

        login = request(
            "POST",
            "/auth/login",
            json={
                "username": "admin",
                "password": (ROOT / ".local/admin-password").read_text().strip(),
            },
        )
        client.headers["Authorization"] = "Bearer " + login["access_token"]
        provider = None
        config = None
        model = None
        try:
            project = request(
                "POST",
                "/projects",
                json={
                    "name": "Agent 验收 · 自然语言流程",
                    "task_type": "text_classification",
                    "description": "合成样例与本地协议测试服务，不代表真实模型质量。",
                    "allow_self_review": True,
                },
            )
            schema = request(
                "POST",
                "/projects/" + project["id"] + "/schemas",
                json={
                    "labels": [{"name": "售后"}, {"name": "物流"}],
                    "rules": {"instructions": "根据用户主要诉求分类。"},
                },
            )
            request("POST", "/schemas/" + schema["id"] + "/publish")
            payload = "\n".join(
                json.dumps({"text": text}, ensure_ascii=False)
                for text in ["希望申请退款。", "查询包裹配送进度。", "如何申请退货？"]
            )
            imported = request(
                "POST",
                "/projects/" + project["id"] + "/imports",
                headers={"Idempotency-Key": str(uuid4())},
                files={"file": ("agent-fixture.jsonl", payload.encode())},
            )
            for _ in range(60):
                job = request("GET", "/jobs/" + imported["id"])
                if job["status"] in ("succeeded", "failed"):
                    break
                time.sleep(0.5)
            assert job["status"] == "succeeded", job
            dataset_id = job["result"]["dataset_id"]
            items = request("GET", "/projects/" + project["id"] + "/items")
            first = items[0]
            saved = request(
                "POST",
                "/items/" + first["id"] + "/revisions",
                json={
                    "schema_id": schema["id"],
                    "expected_version": 1,
                    "operation_id": str(uuid4()),
                    "content": {
                        "task_type": "text_classification",
                        "label_id": schema["labels"][0]["id"],
                    },
                },
            )
            submitted = request(
                "POST",
                "/items/" + first["id"] + "/submit",
                json={
                    "schema_id": schema["id"],
                    "revision_id": saved["revision"]["id"],
                    "expected_version": saved["work"]["version"],
                },
            )
            request(
                "POST",
                "/revisions/" + saved["revision"]["id"] + "/reviews",
                json={
                    "decision": "accept",
                    "expected_version": submitted["version"],
                    "operation_id": str(uuid4()),
                    "comment": "协议验收人工审核样例",
                },
            )
            provider = request(
                "POST",
                "/admin/providers",
                json={
                    "name": "[仅验收] 本地规划协议",
                    "base_url": f"http://127.0.0.1:{port}/v1",
                    "model_name": "contract-planner",
                    "api_key": "local-contract-fixture",
                    "budget_tokens": 1000000,
                },
            )
            request(
                "POST",
                "/admin/providers/" + provider["id"] + "/grant",
                params={"project_id": project["id"]},
            )
            model = request(
                "POST",
                "/admin/models",
                json={
                    "name": "[仅验收] 分类协议服务",
                    "adapter": "http",
                    "task_type": "text_classification",
                    "config": {
                        "endpoint": f"http://127.0.0.1:{port}",
                        "test_fixture": True,
                    },
                },
            )
            skills = request("GET", "/admin/agent-skills")
            config_body = {
                "name": "[仅验收] 自然语言工作流",
                "engine": "http_plan",
                "protocol": "openai",
                "provider_id": provider["id"],
                "skill_ids": [s["id"] for s in skills],
            }
            config = request("POST", "/admin/agent-configs", json=config_body)
            run = request(
                "POST",
                "/projects/" + project["id"] + "/agent-runs",
                headers={"Idempotency-Key": str(uuid4())},
                json={
                    "config_id": config["id"],
                    "instruction": "给所选数据版本的两条未开始文本预标注，汇总低分原因，并导出已人工审核的数据。",
                    "dataset_id": dataset_id,
                    "model_id": model["id"],
                    "max_items": 2,
                    "allowed_actions": ["prediction", "summary", "export"],
                },
            )
            for _ in range(120):
                result = request("GET", "/agent-runs/" + run["id"])
                if result["status"] in (
                    "completed",
                    "failed",
                    "partially_failed",
                    "needs_input",
                ):
                    break
                time.sleep(0.5)
            assert result["status"] == "completed", result
            assert result["summary"]["predicted"] == 2
            report = {
                "project_id": project["id"],
                "run_id": run["id"],
                "config_id": config["id"],
                "provider_id": provider["id"],
                "model_id": model["id"],
                "dataset_id": dataset_id,
                "port": port,
                "summary": result["summary"],
                "engine": "http_plan",
                "model_kind": "controlled_contract_fixture",
            }
            destination = ROOT / ".local/agent-verification"
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "native-smoke.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2)
            )
            print(json.dumps(report, ensure_ascii=False), flush=True)
            if args.serve:
                print(
                    "协议服务保留供浏览器验收；Ctrl+C 停止并停用测试配置。", flush=True
                )
                while True:
                    time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            # uv may relay a second SIGINT; finish deactivating owned fixtures.
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            if config:
                request(
                    "PUT",
                    "/admin/agent-configs/" + config["id"],
                    json={**config_body, "enabled": False},
                )
            if provider:
                request(
                    "PATCH",
                    "/admin/providers/" + provider["id"],
                    json={"enabled": False},
                )
            if model:
                sys.path.insert(0, str(ROOT / "backend"))
                from app.db import session_scope
                from app.models import ModelSpec

                with session_scope() as db:
                    stored = db.get(ModelSpec, model["id"])
                    stored.config = {**stored.config, "candidate": True}
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    main()
