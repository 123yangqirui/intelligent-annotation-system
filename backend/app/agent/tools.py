"""Tools keep actor/project identity on the server and reuse existing application use cases."""

import time
from sqlalchemy import select, func
from fastapi.encoders import jsonable_encoder
from ..db import session_scope
from ..models import (
    AgentRun,
    AgentConfig,
    AgentToolCall,
    User,
    LabelSchema,
    Dataset,
    DatasetItem,
    Item,
    Work,
    ModelSpec,
    ProviderGrant,
    Job,
    JobItem,
    Revision,
)
from ..security import project_access, record
from ..job_control import check_active
from .schemas import Plan

READ_TOOLS = {
    "get_project_context": "查询当前授权项目、规范和可执行动作。",
    "list_dataset_versions": "查询当前项目的数据版本和样本数量。",
    "get_label_schema": "读取本次运行的已发布标签规范。",
    "list_compatible_models": "查询任务能力匹配、已授权且可使用的模型。",
}


def actor_context(db, run):
    actor = db.get(User, run.actor_id, populate_existing=True)
    config = db.get(AgentConfig, run.config_id, populate_existing=True)
    if not actor or not actor.enabled:
        raise ValueError("运行创建者已禁用")
    if not config or not config.enabled:
        raise ValueError("Agent 配置已停用")
    project = project_access(db, actor, run.project_id, ("owner",), True)
    if not db.get(
        ProviderGrant,
        (run.config_snapshot["provider_id"], project.id),
        populate_existing=True,
    ):
        raise ValueError("项目的规划服务授权已撤销")
    return actor, project


def dataset_context(db, project):
    rows = db.execute(
        select(Dataset, func.count(DatasetItem.item_id))
        .outerjoin(DatasetItem, DatasetItem.dataset_id == Dataset.id)
        .where(Dataset.project_id == project.id)
        .group_by(Dataset.id)
        .order_by(Dataset.created_at.desc())
        .limit(100)
    ).all()
    return [
        {"id": d.id, "name": d.name, "status": d.status, "count": count}
        for d, count in rows
    ]


def model_context(db, project):
    result = []
    for model in db.scalars(
        select(ModelSpec)
        .where(ModelSpec.task_type == project.task_type)
        .order_by(ModelSpec.created_at.desc())
        .limit(100)
    ).all():
        from ..inference import ensure_model_available
        from fastapi import HTTPException

        try:
            deployment = ensure_model_available(db, model, project)
        except HTTPException:
            continue
        result.append(
            {
                "id": model.id,
                "name": model.name,
                "adapter": model.adapter,
                "task_type": model.task_type,
                "deployment_status": deployment.status
                if deployment
                else "external_service",
            }
        )
    return result


def build_context(db, run):
    _, project = actor_context(db, run)
    schema = (
        db.get(LabelSchema, run.context.get("schema_id"))
        if run.context.get("schema_id")
        else None
    )
    return {
        "project": {
            "id": project.id,
            "name": project.name,
            "task_type": project.task_type,
        },
        "schema": {
            "id": schema.id,
            "version": schema.version,
            "labels": schema.labels,
            "rules": schema.rules,
        }
        if schema
        else None,
        "datasets": dataset_context(db, project),
        "models": model_context(db, project),
        "selection": run.context,
        "allowed_actions": run.allowed_actions,
        "limits": {
            "max_steps": run.config_snapshot["max_steps"],
            "max_items_per_step": 500,
        },
        "review_policy": "manual_all",
    }


def read_tool(run_id, job_id, lease, name, arguments):
    if name not in READ_TOOLS or arguments:
        raise ValueError("未知工具或参数；项目范围由服务端绑定")
    start = time.monotonic()
    with session_scope() as db:
        job = db.get(Job, job_id)
        check_active(db, job, lease)
        run = db.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
        context = build_context(db, run)
        base = run.turn * 100
        n = db.scalar(
            select(func.count())
            .select_from(AgentToolCall)
            .where(
                AgentToolCall.run_id == run.id,
                AgentToolCall.phase == "planning",
                AgentToolCall.step_index >= base,
                AgentToolCall.step_index < base + 100,
            )
        )
        if n >= 16:
            raise ValueError("规划查询超过 16 次限制")
        result = {
            "get_project_context": {
                "project": context["project"],
                "selection": context["selection"],
                "allowed_actions": run.allowed_actions,
            },
            "list_dataset_versions": context["datasets"],
            "get_label_schema": context["schema"],
            "list_compatible_models": context["models"],
        }[name]
        db.add(
            AgentToolCall(
                run_id=run.id,
                phase="planning",
                step_index=base + n,
                name=name,
                status="succeeded",
                arguments={},
                result={"data": jsonable_encoder(result)},
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        )
        record(
            db,
            "agent.tool_finished",
            run.actor_id,
            run.project_id,
            {"run_id": run.id, "tool": name, "phase": "planning"},
        )
        return result


def prepare_plan(db, run, plan: Plan):
    _, project = actor_context(db, run)
    if len(plan.steps) > run.config_snapshot["max_steps"]:
        raise ValueError("计划步骤超过配置上限")
    schema_id = run.context.get("schema_id")
    schema = db.get(LabelSchema, schema_id) if schema_id else None
    prepared = []
    prediction_total = 0
    compatible = {x["id"] for x in model_context(db, project)}
    for index, step in enumerate(plan.steps):
        if step.action not in run.allowed_actions:
            raise ValueError("计划包含未授权动作")
        if step.action == "summary":
            prepared.append(
                AgentToolCall(
                    run_id=run.id,
                    step_index=index,
                    name="get_prediction_summary",
                    arguments={},
                )
            )
            continue
        if (
            not schema
            or schema.project_id != project.id
            or schema.status != "published"
        ):
            raise ValueError("请先为项目发布标签规范")
        if step.schema_id and str(step.schema_id) != schema_id:
            raise ValueError("计划不能改变本次运行的规范版本")
        dataset_id = (
            str(step.dataset_id) if step.dataset_id else run.context.get("dataset_id")
        )
        if run.context.get("dataset_id") and dataset_id != run.context["dataset_id"]:
            raise ValueError("计划不能改变显式选定的数据版本")
        dataset = db.get(Dataset, dataset_id) if dataset_id else None
        if not dataset or dataset.project_id != project.id or dataset.status != "ready":
            raise ValueError("请选择本项目已发布的数据版本")
        if step.action == "prediction":
            model_id = (
                str(step.model_id) if step.model_id else run.context.get("model_id")
            )
            if run.context.get("model_id") and model_id != run.context["model_id"]:
                raise ValueError("计划不能改变显式选定的标注模型")
            if step.limit > run.context.get("max_items", 100):
                raise ValueError("计划样本数量超过本次运行的上限")
            if model_id not in compatible:
                raise ValueError("模型不属于可用、兼容、已授权的候选集合")
            query = (
                select(Item.id)
                .join(DatasetItem, DatasetItem.item_id == Item.id)
                .where(DatasetItem.dataset_id == dataset.id)
            )
            if step.selection != "all":
                query = query.join(Work, Work.item_id == Item.id).where(
                    Work.schema_id == schema_id, Work.state == step.selection
                )
            ids = db.scalars(
                query.order_by(Item.created_at, Item.id).limit(step.limit)
            ).all()
            if not ids:
                raise ValueError("当前筛选下没有可预标注样本")
            prediction_total += len(ids)
            if prediction_total > run.context.get("max_items", 100):
                raise ValueError("计划累计预标注数量超过本次运行的上限")
            args = {
                "dataset_id": dataset.id,
                "schema_id": schema_id,
                "model_id": model_id,
                "item_ids": list(ids),
            }
            prepared.append(
                AgentToolCall(
                    run_id=run.id,
                    step_index=index,
                    name="create_prediction_job",
                    arguments=args,
                )
            )
        else:
            format = step.format or (
                "jsonl" if project.task_type == "text_classification" else "yolo"
            )
            if (project.task_type == "text_classification") != (
                format in ("jsonl", "csv")
            ):
                raise ValueError("导出格式与项目类型不匹配")
            prepared.append(
                AgentToolCall(
                    run_id=run.id,
                    step_index=index,
                    name="create_reviewed_export",
                    arguments={
                        "dataset_id": dataset.id,
                        "schema_id": schema_id,
                        "format": format,
                    },
                )
            )
    return prepared


def result_summary(db, run):
    steps = db.scalars(
        select(AgentToolCall)
        .where(AgentToolCall.run_id == run.id, AgentToolCall.phase == "execution")
        .order_by(AgentToolCall.step_index)
    ).all()
    result = {
        "jobs": [],
        "predicted": 0,
        "failed_items": 0,
        "review_reasons": {},
        "review_policy": "所有预测仍需人工审核",
    }
    for step in steps:
        if not step.child_job_id:
            continue
        job = db.get(Job, step.child_job_id)
        result["jobs"].append(
            {
                "job_id": job.id,
                "type": job.type,
                "status": job.status,
                "export_id": (job.result or {}).get("export_id"),
            }
        )
        if job.type == "prediction":
            for entry in db.scalars(
                select(JobItem).where(JobItem.job_id == job.id)
            ).all():
                if entry.status == "succeeded":
                    result["predicted"] += 1
                if entry.status == "failed":
                    result["failed_items"] += 1
                if entry.revision_id:
                    revision = db.get(Revision, entry.revision_id)
                    for reason in revision.signals.get("review_reasons", []):
                        result["review_reasons"][reason] = (
                            result["review_reasons"].get(reason, 0) + 1
                        )
    return result


def execute_tool(db, run, call):
    actor, project = actor_context(db, run)
    from .. import api
    from ..schemas import PredictionInput, ExportInput

    key = "agent:" + run.id + ":" + str(call.step_index)
    if call.name == "create_prediction_job":
        return api.prediction_job(
            project.id, PredictionInput(**call.arguments), key=key, user=actor, db=db
        )
    if call.name == "create_reviewed_export":
        return api.add_export(
            project.id, ExportInput(**call.arguments), key=key, user=actor, db=db
        )
    if call.name == "get_prediction_summary":
        return result_summary(db, run)
    raise ValueError("未开放此业务工具")
