"""Agent behavior through real business tools and controlled HTTP/SDK planners."""

import asyncio, json, threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from uuid import uuid4
from pathlib import Path
import pytest
from sqlalchemy import select, func
from app.db import session_scope
from app.models import (
    AgentRun,
    AgentToolCall,
    Job,
    ModelCall,
    Work,
    Provider,
    ProviderGrant,
)
from app.tasks import execute_job, recover_once


@contextmanager
def planner_server(factory=None, predict_failure=False):
    captures = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path.endswith("/chat/completions"):
                context = json.loads(data["messages"][-1]["content"])
                captures.append(context)
                if factory:
                    plan = factory(context)
                else:
                    plan = {
                        "goal": "预标注并汇总",
                        "steps": [
                            {
                                "action": "prediction",
                                "dataset_id": context["context"]["selection"][
                                    "dataset_id"
                                ],
                                "model_id": context["context"]["selection"]["model_id"],
                                "limit": 2,
                            },
                            {"action": "summary"},
                        ],
                    }
                result = {
                    "choices": [{"message": {"content": json.dumps(plan)}}],
                    "usage": {"total_tokens": 40},
                }
            else:
                if predict_failure:
                    self.send_response(500)
                    self.end_headers()
                    return
                if data.get("task_type") == "image_detection":
                    result = {
                        "content": {
                            "task_type": "image_detection",
                            "coordinate_space": "original_image_pixels",
                            "explicit_empty": False,
                            "boxes": [
                                {
                                    "label_id": data["labels"][0]["id"],
                                    "xyxy": [1, 2, 30, 40],
                                    "score": 0.4,
                                }
                            ],
                        },
                        "signals": {"score_kind": "uncalibrated"},
                    }
                else:
                    result = {
                        "content": {
                            "task_type": "text_classification",
                            "label_id": data["labels"][0]["id"],
                        },
                        "signals": {"score": 0.4},
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
    try:
        yield server.server_port, captures
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def setup(
    client, accounts, workspace, port, engine="http_plan", task="text_classification"
):
    project, schema, dataset, headers = workspace(task)
    if task == "image_detection":
        from test_workflow import image_bytes

        response = client.post(
            "/api/v1/projects/" + project["id"] + "/imports",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            files={"file": ("test.png", image_bytes())},
        )
        response.raise_for_status()
        execute_job.run(response.json()["id"])
        job = client.get(
            "/api/v1/jobs/" + response.json()["id"], headers=headers
        ).json()
        dataset = {"id": job["result"]["dataset_id"]}
    for i in range(3 if task == "text_classification" else 0):
        client.post(
            "/api/v1/projects/" + project["id"] + "/texts",
            headers=headers,
            json={"dataset_id": dataset["id"], "text": "样例 " + str(i)},
        ).raise_for_status()
    if task == "text_classification":
        client.post(
            "/api/v1/datasets/" + dataset["id"] + "/publish", headers=headers
        ).raise_for_status()
    provider = client.post(
        "/api/v1/admin/providers",
        headers=accounts["admin"],
        json={
            "name": "规划服务",
            "base_url": f"http://127.0.0.1:{port}/v1",
            "model_name": "controlled-planner",
            "api_key": "test-only-secret",
            "budget_tokens": 1000000,
        },
    ).json()
    client.post(
        "/api/v1/admin/providers/" + provider["id"] + "/grant",
        headers=accounts["admin"],
        params={"project_id": project["id"]},
    ).raise_for_status()
    client.post(
        "/api/v1/admin/agent-skills/seed", headers=accounts["admin"]
    ).raise_for_status()
    skills = client.get("/api/v1/admin/agent-skills", headers=accounts["admin"]).json()
    model = client.post(
        "/api/v1/admin/models",
        headers=accounts["admin"],
        json={
            "name": "测试分类模型",
            "adapter": "http",
            "task_type": task,
            "config": {"endpoint": f"http://127.0.0.1:{port}"},
        },
    ).json()
    config = client.post(
        "/api/v1/admin/agent-configs",
        headers=accounts["admin"],
        json={
            "name": "任务助手",
            "engine": engine,
            "protocol": "anthropic" if engine == "claude_sdk" else "openai",
            "provider_id": provider["id"],
            "skill_ids": [skills[0]["id"]],
        },
    ).json()
    body = {
        "config_id": config["id"],
        "instruction": "用所选模型预标注前两条未标注文本，完成后汇总。",
        "dataset_id": dataset["id"],
        "model_id": model["id"],
    }
    return project, schema, dataset, headers, provider, config, body


def create(client, project, headers, body, key=None):
    response = client.post(
        "/api/v1/projects/" + project["id"] + "/agent-runs",
        headers={**headers, "Idempotency-Key": key or str(uuid4())},
        json=body,
    )
    assert response.status_code == 202, response.text
    return response.json()


def detail(client, run, headers):
    return client.get("/api/v1/agent-runs/" + run["id"], headers=headers).json()


def drive(client, run, headers):
    for _ in range(12):
        current = detail(client, run, headers)
        if current["status"] in (
            "completed",
            "needs_input",
            "failed",
            "partially_failed",
            "cancelled",
        ):
            return current
        if current["status"] == "waiting_job":
            for call in current["tool_calls"]:
                if call["status"] == "waiting_job" and call["child_job_id"]:
                    execute_job.run(call["child_job_id"])
            recover_once()
        else:
            execute_job.run(current["active_job_id"])
    raise AssertionError("Agent did not finish")


def test_agent_preannotation_real_tools_wait_resume_and_no_auto_review(
    client, accounts, workspace
):
    with planner_server() as (port, captures):
        project, schema, _, headers, provider, config, body = setup(
            client, accounts, workspace, port
        )
        run = create(client, project, headers, body)
        execute_job.run(run["active_job_id"])
        current = detail(client, run, headers)
        assert current["status"] == "waiting_job" and len(captures) == 1
        assert "test-only-secret" not in json.dumps(captures)
        result = drive(client, run, headers)
        assert (
            result["status"] == "completed"
            and result["summary"]["predicted"] == 2
            and result["summary"]["review_reasons"]["low_model_score"] == 2
        )
        assert len(captures) == 1
        with session_scope() as db:
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(Job)
                    .where(Job.type == "prediction")
                )
                == 1
            )
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(Work)
                    .where(Work.state == "accepted")
                )
                == 0
            )
            assert db.get(Provider, provider["id"]).spent_tokens == 40
        assert result["skills"][0]["version"] == 1


def test_agent_needs_input_then_resumes_without_reusing_old_plan(
    client, accounts, workspace
):
    def factory(context):
        if not context["additional_input"]:
            return {"goal": "缺少模型说明", "questions": ["请明确模型。"]}
        return {"goal": "汇总当前情况", "steps": [{"action": "summary"}]}

    with planner_server(factory) as (port, captures):
        project, _, _, headers, _, _, body = setup(client, accounts, workspace, port)
        run = create(client, project, headers, body)
        result = drive(client, run, headers)
        assert result["status"] == "needs_input"
        key = str(uuid4())
        response = client.post(
            "/api/v1/agent-runs/" + run["id"] + "/input",
            headers={**headers, "Idempotency-Key": key},
            json={"message": "只汇总当前数据，不进行预测。"},
        )
        assert response.status_code == 202
        duplicate = client.post(
            "/api/v1/agent-runs/" + run["id"] + "/input",
            headers={**headers, "Idempotency-Key": key},
            json={"message": "只汇总当前数据，不进行预测。"},
        )
        assert duplicate.json()["active_job_id"] == response.json()["active_job_id"]
        assert (
            drive(client, run, headers)["status"] == "completed" and len(captures) == 2
        )


def test_agent_ownership_grant_and_idempotency(client, accounts, workspace):
    with planner_server() as (port, _):
        project, _, _, headers, _, config, body = setup(
            client, accounts, workspace, port
        )
        key = str(uuid4())
        run = create(client, project, headers, body, key)
        assert create(client, project, headers, body, key)["id"] == run["id"]
        conflict = client.post(
            "/api/v1/projects/" + project["id"] + "/agent-runs",
            headers={**headers, "Idempotency-Key": key},
            json={**body, "instruction": "其他任务"},
        )
        assert conflict.status_code == 409
        assert (
            client.get(
                "/api/v1/agent-runs/" + run["id"],
                headers=accounts["headers"]["outsider"],
            ).status_code
            == 404
        )
        assert (
            client.get("/api/v1/admin/agent-configs", headers=headers).status_code
            == 403
        )
        with session_scope() as db:
            db.delete(db.get(ProviderGrant, (config["provider_id"], project["id"])))
        assert (
            client.post(
                "/api/v1/projects/" + project["id"] + "/agent-runs",
                headers={**headers, "Idempotency-Key": str(uuid4())},
                json=body,
            ).status_code
            == 403
        )


@pytest.mark.parametrize(
    "step",
    [
        {"action": "prediction", "model_id": "bad-id"},
        {"action": "approve"},
        {"action": "prediction", "limit": 501},
    ],
)
def test_agent_invalid_plans_are_repaired_at_most_once_and_never_write(
    client, accounts, workspace, step
):
    with planner_server(lambda _: {"goal": "无效计划", "steps": [step]}) as (
        port,
        captures,
    ):
        project, _, _, headers, _, _, body = setup(client, accounts, workspace, port)
        result = drive(client, create(client, project, headers, body), headers)
        assert result["status"] == "failed" and len(captures) == 2
        with session_scope() as db:
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(Job)
                    .where(Job.type == "prediction")
                )
                == 0
            )


def test_agent_rejects_hallucinated_resources_and_unauthorized_exports(
    client, accounts, workspace
):
    with planner_server(
        lambda _: {"goal": "导出", "steps": [{"action": "export"}]}
    ) as (port, _):
        project, _, _, headers, _, _, body = setup(client, accounts, workspace, port)
        result = drive(client, create(client, project, headers, body), headers)
        assert result["status"] == "needs_input" and "未授权" in result["questions"][0]
    with planner_server(
        lambda c: {
            "goal": "伪造模型",
            "steps": [
                {
                    "action": "prediction",
                    "dataset_id": c["context"]["selection"]["dataset_id"],
                    "model_id": str(uuid4()),
                }
            ],
        }
    ) as (port, _):
        project, _, _, headers, _, _, body = setup(client, accounts, workspace, port)
        result = drive(client, create(client, project, headers, body), headers)
        assert result["status"] == "needs_input" and any(
            x in result["questions"][0] for x in ("候选", "显式")
        )


def test_agent_cancellation_cancels_owned_child_and_recovery_is_idempotent(
    client, accounts, workspace
):
    with planner_server() as (port, _):
        project, _, _, headers, _, _, body = setup(client, accounts, workspace, port)
        run = create(client, project, headers, body)
        execute_job.run(run["active_job_id"])
        assert detail(client, run, headers)["status"] == "waiting_job"
        assert (
            client.post(
                "/api/v1/agent-runs/" + run["id"] + "/cancel", headers=headers
            ).json()["status"]
            == "cancelled"
        )
        recover_once()
        recover_once()
        with session_scope() as db:
            jobs = db.scalars(select(Job).where(Job.type == "prediction")).all()
            assert len(jobs) == 1 and jobs[0].status == "cancelled"
        assert detail(client, run, headers)["status"] == "cancelled"


def test_agent_child_failure_is_not_reported_as_completed(client, accounts, workspace):
    with planner_server(predict_failure=True) as (port, _):
        project, _, _, headers, _, _, body = setup(client, accounts, workspace, port)
        result = drive(client, create(client, project, headers, body), headers)
        assert result["status"] == "failed" and result["summary"]["failed_items"] == 2


def test_agent_skill_versions_and_config_validation(client, accounts, workspace):
    with planner_server() as (port, _):
        project, _, _, headers, provider, _, body = setup(
            client, accounts, workspace, port
        )
        run = create(client, project, headers, body)
        skill = client.get(
            "/api/v1/admin/agent-skills", headers=accounts["admin"]
        ).json()[0]
        response = client.put(
            "/api/v1/admin/agent-skills/" + skill["id"],
            headers=accounts["admin"],
            json={
                "name": skill["name"],
                "description": skill["description"],
                "content": "新流程",
                "enabled": True,
            },
        )
        assert response.json()["version"] == 2
        assert detail(client, run, headers)["skills"][0]["version"] == 1
        assert (
            client.post(
                "/api/v1/admin/agent-configs",
                headers=accounts["admin"],
                json={
                    "name": "bad",
                    "engine": "claude_sdk",
                    "protocol": "openai",
                    "provider_id": provider["id"],
                },
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/admin/agent-skills",
                headers=accounts["admin"],
                json={
                    "name": "bad-skill",
                    "description": "bad",
                    "content": "---\nallowed-tools: Bash\n---",
                },
            ).status_code
            == 422
        )


def test_sdk_policy_disables_shell_and_constrains_skill_reads(env, tmp_path):
    from app.agent.engines import sdk_options
    from claude_agent_sdk import create_sdk_mcp_server

    config = {
        "model_name": "test",
        "system_prompt": "",
        "max_turns": 2,
        "max_output_tokens": 512,
        "skills": [{"name": "text-preannotation"}],
    }
    options = sdk_options(
        config,
        tmp_path,
        create_sdk_mcp_server("annotation"),
        "test-secret",
        "http://localhost/v1",
    )
    assert (
        options.tools == ["Skill"]
        and options.strict_mcp_config
        and "Bash" in options.disallowed_tools
    )
    assert (
        options.env["ANTHROPIC_API_KEY"] == "test-secret"
        and options.env["ANTHROPIC_AUTH_TOKEN"] == ""
    )
    assert (
        asyncio.run(
            options.can_use_tool("Bash", {"command": "cat .env"}, None)
        ).behavior
        == "deny"
    )
    assert (
        asyncio.run(
            options.can_use_tool("Read", {"file_path": "../../.env"}, None)
        ).behavior
        == "deny"
    )
    path = tmp_path / ".claude/skills/text-preannotation/SKILL.md"
    assert (
        asyncio.run(
            options.can_use_tool("Read", {"file_path": str(path)}, None)
        ).behavior
        == "deny"
    )


def test_sdk_engine_uses_scoped_options_and_records_real_usage(
    client, accounts, workspace, monkeypatch
):
    import claude_agent_sdk
    from claude_agent_sdk import ResultMessage

    seen = []

    class SDK:
        def __init__(self, options):
            self.options = options
            seen.append(options)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def query(self, prompt):
            assert "test-only-secret" not in prompt

        async def receive_response(self):
            yield ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id=str(uuid4()),
                structured_output={"goal": "汇总", "steps": [{"action": "summary"}]},
                usage={"input_tokens": 20, "output_tokens": 10},
            )

    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", SDK)
    with planner_server() as (port, _):
        project, _, _, headers, provider, _, body = setup(
            client, accounts, workspace, port, "claude_sdk"
        )
        result = drive(client, create(client, project, headers, body), headers)
        assert result["status"] == "completed" and len(seen) == 1
        assert not Path(seen[0].cwd).exists()
        with session_scope() as db:
            assert db.get(Provider, provider["id"]).spent_tokens == 30


def test_cancel_covers_child_committed_before_tool_link(client, accounts, workspace):
    from app.agent.tools import execute_tool

    project = None
    with planner_server() as (port, _):
        project, _, _, headers, _, _, body = setup(client, accounts, workspace, port)
        run = create(client, project, headers, body)
        with session_scope() as db:
            current = db.get(AgentRun, run["id"])
            call = AgentToolCall(
                run_id=current.id,
                step_index=0,
                name="create_prediction_job",
                arguments={
                    "dataset_id": body["dataset_id"],
                    "model_id": body["model_id"],
                    "schema_id": current.context["schema_id"],
                },
            )
            db.add(call)
            db.commit()
            result = execute_tool(db, current, call)
            assert call.child_job_id is None
        client.post(
            "/api/v1/agent-runs/" + run["id"] + "/cancel", headers=headers
        ).raise_for_status()
        with session_scope() as db:
            assert db.get(Job, result["id"]).status == "cancelled"


def test_http_anthropic_protocol_and_usage_are_explicit(env, monkeypatch):
    import httpx
    from app.agent.engines import http_plan

    captures = []

    def response(url, **kwargs):
        captures.append((url, kwargs))
        return httpx.Response(
            200,
            json={
                "content": [
                    {
                        "type": "text",
                        "text": '{"goal":"汇总","steps":[{"action":"summary"}]}',
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 20},
            },
        )

    monkeypatch.setattr(httpx, "post", response)
    value, usage = http_plan(
        {
            "protocol": "anthropic",
            "model_name": "test",
            "max_output_tokens": 512,
            "timeout_seconds": 10,
            "system_prompt": "",
        },
        "{}",
        "http://localhost/v1",
        "test-secret",
        lambda: None,
    )
    assert (
        captures[0][0].endswith("/messages")
        and captures[0][1]["headers"]["x-api-key"] == "test-secret"
    )
    assert usage["total_tokens"] == 30


def test_planning_budget_blocks_provider_request(client, accounts, workspace):
    with planner_server() as (port, captures):
        project, _, _, headers, provider, _, body = setup(
            client, accounts, workspace, port
        )
        client.patch(
            "/api/v1/admin/providers/" + provider["id"],
            headers=accounts["admin"],
            json={"budget_tokens": 1},
        ).raise_for_status()
        result = drive(client, create(client, project, headers, body), headers)
        assert result["status"] == "failed" and not captures


def test_sdk_query_cancel_does_not_wait_for_another_message(env, monkeypatch):
    import claude_agent_sdk, time
    from app.agent.engines import sdk_plan_async
    from app.job_control import JobCancelled

    class SDK:
        def __init__(self, options):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def query(self, prompt):
            pass

        async def receive_response(self):
            await asyncio.sleep(20)
            if False:
                yield None

    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", SDK)

    def check():
        raise JobCancelled("cancelled")

    config = {
        "skills": [],
        "model_name": "test",
        "system_prompt": "",
        "max_turns": 2,
        "max_output_tokens": 512,
        "timeout_seconds": 10,
    }
    start = time.monotonic()
    with pytest.raises(JobCancelled):
        asyncio.run(
            sdk_plan_async(
                SimpleNamespace(id=str(uuid4())),
                config,
                "{}",
                "http://localhost/v1",
                "key",
                check,
                "job",
                "lease",
            )
        )
    assert time.monotonic() - start < 3


def test_run_sample_limit_is_enforced_before_side_effects(client, accounts, workspace):
    with planner_server() as (port, _):
        project, _, _, headers, _, _, body = setup(client, accounts, workspace, port)
        result = drive(
            client, create(client, project, headers, {**body, "max_items": 1}), headers
        )
        assert result["status"] == "needs_input" and "上限" in result["questions"][0]


def test_image_agent_tracks_box_scores_and_keeps_human_state(
    client, accounts, workspace
):
    with planner_server() as (port, _):
        project, _, _, headers, _, _, body = setup(
            client, accounts, workspace, port, task="image_detection"
        )
        result = drive(client, create(client, project, headers, body), headers)
        assert result["status"] == "completed" and result["summary"]["predicted"] == 1
        assert result["summary"]["review_reasons"]["low_model_score"] == 1
        with session_scope() as db:
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(Work)
                    .where(Work.state == "accepted")
                )
                == 0
            )


def test_agent_aggregate_sample_limit_rejects_all_steps_before_writes(
    client, accounts, workspace
):
    with planner_server(
        lambda _: {
            "goal": "分两步预标注",
            "steps": [
                {"action": "prediction", "limit": 2},
                {"action": "prediction", "limit": 2},
            ],
        }
    ) as (port, _):
        project, _, _, headers, _, _, body = setup(client, accounts, workspace, port)
        result = drive(
            client, create(client, project, headers, {**body, "max_items": 3}), headers
        )
        assert result["status"] == "needs_input"
        assert "累计预标注数量" in result["questions"][0]
        with session_scope() as db:
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(Job)
                    .where(Job.type == "prediction")
                )
                == 0
            )
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(AgentToolCall)
                    .where(
                        AgentToolCall.run_id == result["id"],
                        AgentToolCall.phase == "execution",
                    )
                )
                == 0
            )


def test_agent_recovered_planning_attempts_are_capped_before_network(
    client, accounts, workspace
):
    with planner_server() as (port, captures):
        project, _, _, headers, _, _, body = setup(client, accounts, workspace, port)
        run = create(client, project, headers, body)
        with session_scope() as db:
            db.get(AgentRun, run["id"]).planning_attempts = 2
        result = drive(client, run, headers)
        assert result["status"] == "failed" and "累计重试上限" in result["error"]
        assert not captures
        with session_scope() as db:
            assert db.scalar(select(func.count()).select_from(ModelCall)) == 0
