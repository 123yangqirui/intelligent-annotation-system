"""Durable planning/execution; long child jobs release the Agent worker and resume later."""

import time
from sqlalchemy import select, update
from fastapi import HTTPException
from pydantic import ValidationError
from ..models import (
    AgentRun,
    AgentToolCall,
    Job,
    ModelCall,
    Provider,
    User,
)
from ..security import record, crypt
from ..inference import reserve, settle, validate_url, valid_usage
from ..db import session_scope
from . import engines
from .tools import (
    actor_context,
    build_context,
    prepare_plan,
    execute_tool,
    result_summary,
)

TERMINAL = {"succeeded", "partially_failed", "failed", "cancelled"}


def schedule(db, run):
    from ..tasks import create_job

    user = db.get(User, run.actor_id)
    job = create_job(
        db,
        user,
        run.project_id,
        "agent",
        {"run_id": run.id},
        "agent-turn:" + run.id + ":" + str(run.turn),
    )
    run.active_job_id = job.id
    run.status = "queued"
    return job


def append_event(db, run, action, summary=None):
    record(
        db, action, run.actor_id, run.project_id, {"run_id": run.id, **(summary or {})}
    )


def stop_run(db, run, status, message=None):
    run.status = status
    run.error = message
    if run.planning_call_id:
        call = db.get(ModelCall, run.planning_call_id)
        if call and call.status == "reserved":
            call.status = "usage_unknown"
    append_event(db, run, "agent." + status, {"message": message} if message else {})


def cancel_run(db, run):
    run = db.scalar(
        select(AgentRun)
        .where(AgentRun.id == run.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run.status in ("completed", "partially_failed", "failed", "cancelled"):
        return
    stop_run(db, run, "cancelled")
    from ..models import JobItem

    def stop_job(job):
        if not job:
            return
        if job.status == "queued":
            job.status = "cancelled"
            db.execute(
                update(JobItem)
                .where(JobItem.job_id == job.id)
                .values(status="cancelled")
            )
        elif job.status == "running":
            job.status = "cancel_requested"

    parent = (
        db.scalar(
            select(Job)
            .where(Job.id == run.active_job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if run.active_job_id
        else None
    )
    stop_job(parent)
    # A child can commit before the tool-call link is saved. Its stable key still
    # identifies it, including cancellation racing that narrow recovery window.
    children = db.scalars(
        select(Job)
        .where(
            Job.actor_id == run.actor_id,
            Job.project_id == run.project_id,
            Job.idempotency_key.like("agent:" + run.id + ":%"),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    for child in children:
        stop_job(child)


def plan_run(db, run, job, check):
    config = run.config_snapshot
    provider = db.get(Provider, config["provider_id"], populate_existing=True)
    if not provider or not provider.enabled:
        raise ValueError("规划服务不存在或已停用")
    base_url = validate_url(provider.base_url)
    try:
        key = crypt().decrypt(provider.encrypted_key.encode()).decode()
    except Exception:
        raise ValueError("规划服务密钥无法解密，请检查主密钥与供应商配置")
    config = {**config, "model_name": provider.model_name}
    repair = ""
    for attempt in range(2):
        check()
        # Persisted attempts also bound paid replanning after worker crashes.
        if run.planning_attempts >= 2 * (len(run.inputs) + 1):
            raise ValueError("规划调用已达到累计重试上限，请创建新运行")
        context = build_context(db, run)
        prompt = engines.make_prompt(run, context, repair)
        amount = len(prompt.encode()) + config["max_output_tokens"] + 128
        if config["engine"] == "claude_sdk":
            amount = (
                amount + 32768 + config["max_output_tokens"] * config["max_turns"]
            ) * config["max_turns"]
        run.status = "planning"
        run.planning_attempts += 1
        db.commit()
        call_id = reserve(db, provider, job, prompt, amount_override=amount)
        run.planning_call_id = call_id
        db.commit()
        usage = None
        try:
            value, usage = engines.plan(
                config, prompt, base_url, key, run, check, job.id, job.lease_token
            )
            check()
            plan = engines.parse_plan(value)
            if plan.questions:
                run.plan = plan.model_dump(mode="json")
                run.questions = plan.questions
                run.status = "needs_input"
                run.summary = {"message": "需要补充信息后继续。"}
                append_event(
                    db, run, "agent.needs_input", {"questions": plan.questions}
                )
            else:
                try:
                    calls = prepare_plan(db, run, plan)
                except ValueError as error:
                    run.status = "needs_input"
                    run.questions = [str(error)]
                    run.error = None
                    append_event(
                        db, run, "agent.needs_input", {"questions": run.questions}
                    )
                else:
                    run.plan = plan.model_dump(mode="json")
                    run.questions = []
                    run.status = "executing"
                    db.add_all(calls)
                    append_event(
                        db,
                        run,
                        "agent.planned",
                        {"goal": plan.goal, "steps": len(calls)},
                    )
            check()
            settle(
                db,
                call_id,
                usage,
                "succeeded" if valid_usage(usage) else "usage_unknown",
            )
            return
        except engines.EngineFailure as error:
            settle(
                db,
                call_id,
                error.usage,
                "rejected"
                if error.rejected
                else "succeeded"
                if valid_usage(error.usage)
                else "usage_unknown",
            )
            raise
        except (ValidationError, ValueError, TypeError) as error:
            db.rollback()
            settle(
                db,
                call_id,
                usage,
                "succeeded" if valid_usage(usage) else "usage_unknown",
            )
            repair = "计划 JSON 不符合协议。请检查 questions/steps 互斥、动作白名单、真实 UUID 和数值限制。"
            if attempt == 1:
                raise ValueError(
                    "规划两次仍未产生有效计划，请修改指令或使用结构化任务表单"
                ) from error
        except BaseException:
            db.rollback()
            settle(
                db,
                call_id,
                usage,
                "succeeded" if valid_usage(usage) else "usage_unknown",
            )
            raise


def run_turn(db, job, project, check):
    run = db.get(AgentRun, job.config["run_id"])
    if not run or run.active_job_id != job.id:
        raise ValueError("Agent 运行与当前任务不匹配")
    if run.status in (
        "cancelled",
        "completed",
        "partially_failed",
        "failed",
        "needs_input",
    ):
        return {"run_id": run.id, "status": run.status}
    actor_context(db, run)
    check()
    if not run.plan:
        if not run.context.get("schema_id") and project.active_schema_id:
            run.context = {**run.context, "schema_id": project.active_schema_id}
        plan_run(db, run, job, check)
        if run.status == "needs_input":
            return {"run_id": run.id, "status": run.status}
    calls = db.scalars(
        select(AgentToolCall)
        .where(AgentToolCall.run_id == run.id, AgentToolCall.phase == "execution")
        .order_by(AgentToolCall.step_index)
    ).all()
    for call in calls:
        check()
        actor_context(db, run)
        if call.status == "succeeded":
            continue
        if call.child_job_id:
            child = db.get(Job, call.child_job_id, populate_existing=True)
            if child.status not in TERMINAL:
                run.status = "waiting_job"
                db.commit()
                return {
                    "run_id": run.id,
                    "status": run.status,
                    "waiting_job_id": child.id,
                }
            call.result = {
                "job_id": child.id,
                "status": child.status,
                "result": child.result,
                "errors": child.errors,
            }
            if child.status != "succeeded":
                call.status = "failed"
                call.error = "子任务未全部成功，请查看逐项错误。"
                run.status = (
                    "partially_failed"
                    if child.status == "partially_failed"
                    or any(x.status == "succeeded" for x in calls)
                    else "failed"
                )
                run.error = call.error
                run.summary = result_summary(db, run)
                append_event(db, run, "agent." + run.status)
                db.commit()
                return {"run_id": run.id, "status": run.status}
            call.status = "succeeded"
            append_event(
                db, run, "agent.tool_finished", {"tool": call.name, "job_id": child.id}
            )
            db.commit()
            continue
        start = time.monotonic()
        call.status = "executing"
        db.commit()
        try:
            from ..job_control import check_active

            check_active(db, job, job.lease_token, lock=True)
            value = execute_tool(db, run, call)
            check()
            call.duration_ms = int((time.monotonic() - start) * 1000)
            if call.name.startswith("create_"):
                call.child_job_id = value["id"]
                call.result = {"job_id": value["id"], "status": value["status"]}
                call.status = "waiting_job"
                run.status = "waiting_job"
                append_event(
                    db,
                    run,
                    "agent.task_submitted",
                    {"tool": call.name, "job_id": value["id"]},
                )
                db.commit()
                return {
                    "run_id": run.id,
                    "status": run.status,
                    "waiting_job_id": value["id"],
                }
            call.result = value
            call.status = "succeeded"
            append_event(db, run, "agent.tool_finished", {"tool": call.name})
            db.commit()
        except HTTPException as error:
            db.rollback()
            call = db.get(AgentToolCall, call.id)
            run = db.get(AgentRun, run.id)
            call.status = "failed"
            call.error = (
                error.detail.get("message", "工具执行失败")
                if isinstance(error.detail, dict)
                else "工具执行失败"
            )
            run.status = (
                "partially_failed"
                if any(x.status == "succeeded" for x in calls)
                else "failed"
            )
            run.error = call.error
            run.summary = result_summary(db, run)
            append_event(db, run, "agent." + run.status)
            db.commit()
            return {"run_id": run.id, "status": run.status}
    check()
    run.status = "completed"
    run.error = None
    run.summary = result_summary(db, run)
    run.summary = {
        **run.summary,
        "message": f"流程完成：成功生成 {run.summary['predicted']} 条预标注，失败 {run.summary['failed_items']} 条；预测仍需人工审核。",
    }
    append_event(db, run, "agent.completed", {"predicted": run.summary["predicted"]})
    db.commit()
    return {"run_id": run.id, "status": run.status}


def recover_agents():
    with session_scope() as db:
        runs = db.scalars(
            select(AgentRun)
            .where(
                AgentRun.status.in_(["queued", "planning", "executing", "waiting_job"])
            )
            .with_for_update(skip_locked=True)
        ).all()
        for run in runs:
            parent = db.get(Job, run.active_job_id) if run.active_job_id else None
            if parent and parent.status in ("failed", "cancelled"):
                stop_run(
                    db,
                    run,
                    "cancelled" if parent.status == "cancelled" else "failed",
                    "Agent 执行任务已停止。",
                )
                continue
            if run.status != "waiting_job" or (
                parent and parent.status not in TERMINAL
            ):
                continue
            call = db.scalar(
                select(AgentToolCall)
                .where(
                    AgentToolCall.run_id == run.id,
                    AgentToolCall.phase == "execution",
                    AgentToolCall.status == "waiting_job",
                )
                .order_by(AgentToolCall.step_index)
            )
            child = (
                db.get(Job, call.child_job_id) if call and call.child_job_id else None
            )
            if child and child.status in TERMINAL:
                run.turn += 1
                schedule(db, run)


def job_stopped(db, job, status, message=None):
    run = db.get(AgentRun, job.config.get("run_id"))
    if (
        run
        and run.active_job_id == job.id
        and run.status
        not in ("completed", "cancelled", "partially_failed", "failed", "needs_input")
    ):
        stop_run(db, run, status, message)
