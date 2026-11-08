"""Live HTTP/Redis/Celery acceptance with synthetic, explicitly marked fixtures.

Run from backend: uv run python ../scripts/runtime_acceptance.py [--serve]
No real model/API credentials, Docker execution or GPU accuracy claims.
"""

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import secrets
import signal
import sys
import threading
import time
from uuid import uuid4
import zipfile

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
DESTINATION = ROOT / ".local/runtime-acceptance"


def main():
    parser = argparse.ArgumentParser(description="运行真实服务的合成数据验收")
    parser.add_argument("--serve", action="store_true", help="保留资料服务供界面检查")
    args = parser.parse_args()
    DESTINATION.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report = {"started_at": stamp, "fixture": True, "checks": [], "projects": {}}
    counts = {"prediction": 0, "planning": 0}
    slow_started = threading.Event()
    release_slow = threading.Event()
    image = io.BytesIO()
    Image.new("RGB", (100, 80), "white").save(image, "PNG")
    image = image.getvalue()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, data, code=200, content_type="application/json"):
            binary = data if isinstance(data, bytes) else json.dumps(data).encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(binary)))
            self.end_headers()
            self.wfile.write(binary)

        def do_GET(self):
            if self.path == "/source.png":
                self.send(image, content_type="image/png")
            elif self.path == "/source":
                self.send(
                    b"<html><body><nav>IGNORE NAV</nav><main>Public fixture article for annotation.</main><script>SECRET SCRIPT</script></body></html>",
                    content_type="text/html",
                )
            else:
                self.send({"data": [{"id": "controlled-fixture"}], "status": "ok"})

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path.endswith("/chat/completions"):
                payload = json.loads(data["messages"][-1]["content"])
                if "context" in payload:
                    counts["planning"] += 1
                    selection = payload["context"]["selection"]
                    if (
                        "clarify" in payload["instruction"]
                        and not payload["additional_input"]
                    ):
                        plan = {"goal": "需要补充", "questions": ["请确认是否只汇总。"]}
                    elif payload["additional_input"]:
                        plan = {"goal": "只汇总", "steps": [{"action": "summary"}]}
                    else:
                        plan = {
                            "goal": "预标注与汇总",
                            "steps": [
                                {
                                    "action": "prediction",
                                    "dataset_id": selection["dataset_id"],
                                    "model_id": selection["model_id"],
                                    "limit": 1,
                                },
                                {"action": "summary"},
                            ],
                        }
                    content = plan
                else:
                    counts["prediction"] += 1
                    content = {"label_id": payload["labels"][0]["id"]}
                self.send(
                    {
                        "choices": [{"message": {"content": json.dumps(content)}}],
                        "usage": {"total_tokens": 40},
                    }
                )
                return
            counts["prediction"] += 1
            if self.path.startswith("/slow"):
                slow_started.set()
                release_slow.wait(30)
            if data["task_type"] == "image_detection":
                content = {
                    "task_type": "image_detection",
                    "coordinate_space": "original_image_pixels",
                    "explicit_empty": False,
                    "boxes": [
                        {"label_id": data["labels"][0]["id"], "xyxy": [1, 2, 60, 70]}
                    ],
                }
                signals = {"box_scores": [0.4]}
            else:
                content = {
                    "task_type": "text_classification",
                    "label_id": data["labels"][0]["id"],
                }
                signals = {"score": 0.4}
            self.send({"content": content, "signals": signals})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    report["source_url"] = base + "/source"
    report["image_url"] = base + "/source.png"
    users, models, provider, config = [], [], None, None
    clients = {}
    success = False
    with ExitStack() as stack:

        def client(name, password):
            session = stack.enter_context(
                httpx.Client(base_url="http://127.0.0.1:8000/api/v1", timeout=30)
            )
            login = session.post(
                "/auth/login", json={"username": name, "password": password}
            )
            login.raise_for_status()
            session.headers["Authorization"] = "Bearer " + login.json()["access_token"]
            return session

        def request(method, path, actor="owner", expected=None, **kwargs):
            session = clients[actor]
            response = session.request(method, path, **kwargs)
            if response.status_code == 401 and expected != 401:
                refresh = session.post("/auth/refresh")
                refresh.raise_for_status()
                session.headers["Authorization"] = (
                    "Bearer " + refresh.json()["access_token"]
                )
                response = session.request(method, path, **kwargs)
            if expected is not None:
                assert response.status_code == expected, (
                    path,
                    response.status_code,
                    response.text[:600],
                )
            else:
                response.raise_for_status()
            return response

        def api(method, path, **kwargs):
            return request(method, path, **kwargs).json()

        def check(name, evidence):
            report["checks"].append(
                {"name": name, "passed": True, "evidence": evidence}
            )
            print("通过：" + name, flush=True)
            save_report()

        def save_report():
            (DESTINATION / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2)
            )

        def wait_job(job, statuses=("succeeded",), actor="owner"):
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                value = api("GET", "/jobs/" + job["id"], actor=actor)
                if value["status"] in (
                    "succeeded",
                    "partially_failed",
                    "failed",
                    "cancelled",
                ):
                    assert value["status"] in statuses, value
                    return value
                time.sleep(0.2)
            raise AssertionError("任务超时：" + job["id"])

        def submit_job(path, body=None, **kwargs):
            return api(
                "POST",
                path,
                headers={"Idempotency-Key": str(uuid4())},
                json=body,
                **kwargs,
            )

        def wait_run(run, target="completed"):
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                value = api("GET", "/agent-runs/" + run["id"])
                if value["status"] in (
                    "completed",
                    "failed",
                    "partially_failed",
                    "needs_input",
                    "cancelled",
                ):
                    assert value["status"] == target, value
                    return value
                time.sleep(0.2)
            raise AssertionError("Agent 超时")

        try:
            clients["admin"] = client(
                "admin", (ROOT / ".local/admin-password").read_text().strip()
            )
            password = secrets.token_urlsafe(24)
            for role in ("owner", "reviewer", "annotator", "outsider"):
                name = "qa_" + role + "_" + stamp
                user = api(
                    "POST",
                    "/admin/users",
                    actor="admin",
                    json={"username": name, "password": password},
                )
                users.append(user["id"])
                clients[role] = client(name, password)
            response = httpx.get("http://127.0.0.1:8000/api/v1/projects")
            assert response.status_code == 401
            assert "X-Request-ID" in response.headers
            # Verify the actual refresh cookie rotation/replay over HTTP in its own session.
            with httpx.Client(base_url="http://127.0.0.1:8000/api/v1") as auth:
                auth.post(
                    "/auth/login",
                    json={"username": "qa_outsider_" + stamp, "password": password},
                ).raise_for_status()
                old = auth.cookies.get("refresh_token")
                assert auth.post("/auth/refresh").status_code == 200
                assert (
                    auth.post(
                        "/auth/refresh", headers={"Cookie": "refresh_token=" + old}
                    ).status_code
                    == 401
                )
                assert auth.post("/auth/refresh").status_code == 401
            check(
                "身份与会话", "未登录 401、请求编号、真实 Cookie 轮换和旧令牌重放阻止"
            )

            for task in ("text_classification", "image_detection"):
                project = api(
                    "POST",
                    "/projects",
                    json={
                        "name": "全模块验收 · " + task + " · " + stamp,
                        "task_type": task,
                        "description": "专用合成验收数据；协议模型不代表实际准确率。",
                    },
                )
                pid = project["id"]
                schema = api(
                    "POST",
                    f"/projects/{pid}/schemas",
                    json={"labels": [{"name": "A"}, {"name": "B"}]},
                )
                api("POST", "/schemas/" + schema["id"] + "/publish")
                request(
                    "PUT",
                    "/schemas/" + schema["id"],
                    expected=409,
                    json={"labels": [{"name": "X"}]},
                )
                for role in ("reviewer", "annotator"):
                    api(
                        "POST",
                        f"/projects/{pid}/members",
                        params={
                            "user_id": users[
                                ("owner", "reviewer", "annotator", "outsider").index(
                                    role
                                )
                            ],
                            "role": role,
                        },
                    )
                request("GET", "/projects/" + pid, actor="outsider", expected=404)
                request(
                    "POST",
                    "/projects/" + pid + "/datasets",
                    actor="annotator",
                    expected=403,
                )
                if task == "text_classification":
                    binary, filename = (
                        b'{"text":"fixture one"}\n{"text":"fixture two"}\n{"text":"fixture three"}\n',
                        "fixture.jsonl",
                    )
                else:
                    binary, filename = image, "fixture.png"
                imported = api(
                    "POST",
                    f"/projects/{pid}/imports",
                    headers={"Idempotency-Key": str(uuid4())},
                    files={"file": (filename, binary)},
                )
                finished = wait_job(imported)
                dataset = finished["result"]["dataset_id"]
                items = api("GET", f"/projects/{pid}/items")
                item = items[0]
                sid = schema["id"]
                report["projects"][task] = {
                    "id": pid,
                    "schema_id": sid,
                    "dataset_id": dataset,
                    "item_id": item["id"],
                }
                content = (
                    {"task_type": task, "label_id": schema["labels"][0]["id"]}
                    if task == "text_classification"
                    else {
                        "task_type": task,
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
                )
                api(
                    "POST",
                    "/items/" + item["id"] + "/claim",
                    actor="annotator",
                    params={"schema_id": sid},
                )
                body = {
                    "schema_id": sid,
                    "content": content,
                    "expected_version": item["work"]["version"],
                    "operation_id": str(uuid4()),
                }
                request(
                    "POST",
                    "/items/" + item["id"] + "/revisions",
                    expected=409,
                    json=body,
                )
                api(
                    "DELETE",
                    "/items/" + item["id"] + "/claim",
                    actor="annotator",
                    params={"schema_id": sid},
                )
                if task == "image_detection":
                    invalid = {
                        **content,
                        "boxes": [{**content["boxes"][0], "xyxy": [-1, 2, 60, 70]}],
                    }
                    request(
                        "POST",
                        "/items/" + item["id"] + "/revisions",
                        expected=400,
                        json={**body, "content": invalid},
                    )
                    assert (
                        request("GET", "/items/" + item["id"] + "/file").content
                        == image
                    )
                saved = api("POST", "/items/" + item["id"] + "/revisions", json=body)
                repeated = api("POST", "/items/" + item["id"] + "/revisions", json=body)
                assert repeated["revision"]["id"] == saved["revision"]["id"]
                request(
                    "POST",
                    "/items/" + item["id"] + "/revisions",
                    expected=409,
                    json={**body, "operation_id": str(uuid4())},
                )
                submit = {
                    "schema_id": sid,
                    "revision_id": saved["revision"]["id"],
                    "expected_version": saved["work"]["version"],
                }
                submitted = api("POST", "/items/" + item["id"] + "/submit", json=submit)
                review = {
                    "decision": "return",
                    "expected_version": submitted["version"],
                    "operation_id": str(uuid4()),
                    "comment": "请检查类别或框坐标",
                }
                request(
                    "POST",
                    "/revisions/" + saved["revision"]["id"] + "/reviews",
                    expected=403,
                    json=review,
                )
                returned = api(
                    "POST",
                    "/revisions/" + saved["revision"]["id"] + "/reviews",
                    actor="reviewer",
                    json=review,
                )
                detail = api("GET", "/items/" + item["id"])
                assert detail["work"]["state"] == "returned"
                saved = api(
                    "POST",
                    "/items/" + item["id"] + "/revisions",
                    json={
                        **body,
                        "expected_version": detail["work"]["version"],
                        "operation_id": str(uuid4()),
                    },
                )
                submitted = api(
                    "POST",
                    "/items/" + item["id"] + "/submit",
                    json={
                        **submit,
                        "revision_id": saved["revision"]["id"],
                        "expected_version": saved["work"]["version"],
                    },
                )
                api(
                    "POST",
                    "/revisions/" + saved["revision"]["id"] + "/reviews",
                    actor="reviewer",
                    json={
                        "decision": "accept",
                        "expected_version": submitted["version"],
                        "operation_id": str(uuid4()),
                    },
                )
                assert api("GET", "/items/" + item["id"])["work"]["state"] == "accepted"
                check(
                    task + " 标注与审核",
                    "真实导入、权限、领取冲突、幂等、版本冲突、退回再提交、独立审核",
                )
                fork = api("POST", "/datasets/" + dataset + "/fork")
                split = api(
                    "POST",
                    "/datasets/" + fork["id"] + "/split",
                    params={"val_fraction": 0.34, "test_fraction": 0},
                )
                assert sum(split.values()) == len(items)
                api("POST", "/datasets/" + fork["id"] + "/publish")
                formats = (
                    ("jsonl", "csv")
                    if task == "text_classification"
                    else ("yolo", "coco")
                )
                for format in formats:
                    exported = wait_job(
                        submit_job(
                            f"/projects/{pid}/exports",
                            {"dataset_id": dataset, "schema_id": sid, "format": format},
                        )
                    )
                    route = "/exports/" + exported["result"]["export_id"] + "/download"
                    binary = request("GET", route).content
                    request("GET", route, actor="outsider", expected=404)
                    with zipfile.ZipFile(io.BytesIO(binary)) as archive:
                        assert "manifest.json" in archive.namelist()
                        if format == "jsonl":
                            rows = (
                                archive.read("annotations.jsonl").decode().splitlines()
                            )
                            assert (
                                len(rows) == 1
                                and json.loads(rows[0])["revision_id"]
                                == saved["revision"]["id"]
                            )
                        elif format == "csv":
                            assert "fixture" in archive.read("annotations.csv").decode()
                        elif format == "coco":
                            assert (
                                len(
                                    json.loads(archive.read("annotations.json"))[
                                        "annotations"
                                    ]
                                )
                                == 1
                            )
                        else:
                            assert "data.yaml" in archive.namelist()
                    (DESTINATION / (task + "_" + format + ".zip")).write_bytes(binary)
                check(
                    task + " 版本与导出",
                    ", ".join(formats)
                    + " 真队列导出、下载内容及越权阻止、版本分叉与划分",
                )
                model = api(
                    "POST",
                    "/admin/models",
                    actor="admin",
                    json={
                        "name": "[仅验收] " + task + "_" + stamp,
                        "adapter": "http",
                        "task_type": task,
                        "config": {"endpoint": base, "test_fixture": True},
                    },
                )
                models.append(model["id"])
                report["projects"][task]["model_id"] = model["id"]
                prediction = {
                    "dataset_id": dataset,
                    "schema_id": sid,
                    "model_id": model["id"],
                    "item_ids": [item["id"]],
                }
                before = counts["prediction"]
                wait_job(submit_job(f"/projects/{pid}/prediction-jobs", prediction))
                assert counts["prediction"] == before + 1
                wait_job(submit_job(f"/projects/{pid}/prediction-jobs", prediction))
                assert counts["prediction"] == before + 1, "Redis 模型缓存未命中"
                assert api("GET", "/items/" + item["id"])["work"]["state"] == "accepted"
                check(
                    task + " 模型与缓存",
                    "真实 HTTP 协议请求、Redis 第二次命中、已有审核状态保持",
                )

            text = report["projects"]["text_classification"]
            pid, sid, dataset = text["id"], text["schema_id"], text["dataset_id"]
            provider = api(
                "POST",
                "/admin/providers",
                actor="admin",
                json={
                    "name": "[仅验收] " + stamp,
                    "base_url": base + "/v1",
                    "model_name": "controlled-fixture",
                    "api_key": "local-only-fixture",
                    "budget_tokens": 1000000,
                },
            )
            api(
                "POST",
                "/admin/providers/" + provider["id"] + "/grant",
                actor="admin",
                params={"project_id": pid},
            )
            assert api(
                "POST", "/admin/providers/" + provider["id"] + "/test", actor="admin"
            )["ok"]
            llm = api(
                "POST",
                "/admin/models",
                actor="admin",
                json={
                    "name": "[仅验收] LLM " + stamp,
                    "adapter": "llm",
                    "task_type": "text_classification",
                    "provider_id": provider["id"],
                    "config": {"test_fixture": True},
                },
            )
            models.append(llm["id"])
            prediction = {
                "dataset_id": dataset,
                "schema_id": sid,
                "model_id": llm["id"],
                "item_ids": [text["item_id"]],
            }
            wait_job(submit_job(f"/projects/{pid}/prediction-jobs", prediction))
            usage = api("GET", "/projects/" + pid + "/usage")
            state = next(
                p
                for p in api("GET", "/admin/providers", actor="admin")
                if p["id"] == provider["id"]
            )
            assert usage
            api(
                "PATCH",
                "/admin/providers/" + provider["id"],
                actor="admin",
                json={
                    "budget_tokens": max(
                        1, state["spent_tokens"] + state["reserved_tokens"]
                    )
                },
            )
            denied = wait_job(
                submit_job(
                    f"/projects/{pid}/prediction-jobs",
                    {
                        **prediction,
                        "item_ids": [api("GET", f"/projects/{pid}/items")[1]["id"]],
                    },
                ),
                statuses=("failed",),
            )
            assert any("预算" in (item["error"] or "") for item in denied["items"])
            api(
                "PATCH",
                "/admin/providers/" + provider["id"],
                actor="admin",
                json={"budget_tokens": 1000000},
            )
            check(
                "大模型 API 与预算",
                "连接测试、真实协议调用、用量账本、预算不足明确失败",
            )
            skill_ids = [
                s["id"] for s in api("GET", "/admin/agent-skills", actor="admin")
            ]
            config_body = {
                "name": "[仅验收] Agent " + stamp,
                "engine": "http_plan",
                "protocol": "openai",
                "provider_id": provider["id"],
                "skill_ids": skill_ids,
            }
            config = api(
                "POST", "/admin/agent-configs", actor="admin", json=config_body
            )
            report["agent_config_id"] = config["id"]
            body = {
                "config_id": config["id"],
                "instruction": "给所选版本一条未开始文本预标注并汇总",
                "dataset_id": dataset,
                "model_id": text["model_id"],
                "max_items": 1,
            }
            run = wait_run(submit_job(f"/projects/{pid}/agent-runs", body))
            assert run["summary"]["predicted"] == 1
            report["agent_run_id"] = run["id"]
            run = wait_run(
                submit_job(
                    f"/projects/{pid}/agent-runs",
                    {**body, "instruction": "clarify 只汇总"},
                ),
                target="needs_input",
            )
            wait_run(
                submit_job(
                    "/agent-runs/" + run["id"] + "/input", {"message": "只汇总当前结果"}
                )
            )
            check("Agent", "规划、真实业务工具、等待与恢复、补充指令与继续")

            slow = api(
                "POST",
                "/admin/models",
                actor="admin",
                json={
                    "name": "[仅验收] cancel " + stamp,
                    "adapter": "http",
                    "task_type": "text_classification",
                    "config": {"endpoint": base + "/slow", "test_fixture": True},
                },
            )
            models.append(slow["id"])
            job = submit_job(
                f"/projects/{pid}/prediction-jobs",
                {**prediction, "model_id": slow["id"]},
            )
            assert slow_started.wait(30), "Worker 未开始受控慢请求"
            api("POST", "/jobs/" + job["id"] + "/cancel")
            release_slow.set()
            wait_job(job, statuses=("cancelled",))
            check("运行中取消", "模型请求进行中取消，响应后任务终止")
            if not api("GET", "/admin/system", actor="admin")["docker_available"]:
                deploy = submit_job(
                    "/admin/deployments",
                    {
                        "model_id": text["model_id"],
                        "template": "bert",
                        "checkpoint": "org/model",
                        "port": 8198,
                    },
                    actor="admin",
                )
                failed = wait_job(deploy, statuses=("failed",), actor="admin")
                assert "Docker" in str(failed["errors"])
                check(
                    "部署环境失败处理",
                    "实际部署队列明确报告 Docker 不可用；未运行真实模型",
                )
            # Insufficient train/val reviewed data must fail rather than train on test.
            training = submit_job(
                f"/projects/{pid}/training-runs",
                {
                    "dataset_id": dataset,
                    "schema_id": sid,
                    "recipe": "bert",
                    "config": {"checkpoint": "org/model", "epochs": 1},
                },
            )
            failed = wait_job(training, statuses=("failed",))
            assert "train" in str(failed["errors"])
            check("训练数据保护", "只有 train 已审核数据时明确阻止训练，要求独立 val")
            assert api("GET", "/projects/" + pid + "/audit")
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                status = api("GET", "/admin/system", actor="admin")
                if status["outbox_pending"] == 0:
                    break
                time.sleep(0.2)
            assert all(
                status[k]
                for k in ("postgresql", "mongodb", "redis_broker", "redis_cache")
            )
            assert status["outbox_pending"] == 0
            assert api("GET", "/admin/events", actor="admin")
            check(
                "基础设施与事件",
                "PostgreSQL/MongoDB/双 Redis 在线，业务审计、Mongo 事件、Outbox 清空",
            )
            report["model_requests"] = counts.copy()
            report["status"] = "passed"
            success = True
            save_report()
            print("验收报告：" + str(DESTINATION / "report.json"), flush=True)
            if args.serve:
                print("资料测试服务保留：" + base + "/source", flush=True)
                while True:
                    time.sleep(1)
        except KeyboardInterrupt:
            pass
        except Exception as error:
            report["status"] = "failed"
            report["failure"] = str(error)[:2000]
            save_report()
            raise
        finally:
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            release_slow.set()
            if config:
                api(
                    "PUT",
                    "/admin/agent-configs/" + config["id"],
                    actor="admin",
                    json={**config_body, "enabled": False},
                )
            if provider:
                api(
                    "PATCH",
                    "/admin/providers/" + provider["id"],
                    actor="admin",
                    json={"enabled": False},
                )
            if models:
                sys.path.insert(0, str(ROOT / "backend"))
                from app.db import session_scope
                from app.models import ModelSpec
                from sqlalchemy import select

                with session_scope() as db:
                    for model in db.scalars(
                        select(ModelSpec).where(ModelSpec.config.is_not(None))
                    ).all():
                        if model.config.get("test_fixture") and (
                            model.id in models or stamp in model.name
                        ):
                            model.config = {**model.config, "candidate": True}
            for uid in users:
                api(
                    "PATCH",
                    "/admin/users/" + uid,
                    actor="admin",
                    params={"enabled": False},
                )
            server.shutdown()
            server.server_close()
            thread.join()
    if not success:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
