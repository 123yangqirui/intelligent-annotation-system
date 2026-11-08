from fastapi import APIRouter, Depends, Header, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from ..db import get_db
from ..models import (
    AgentConfig,
    AgentSkill,
    AgentRun,
    AgentToolCall,
    Provider,
    ProviderGrant,
)
from ..security import current_user, admin, project_access, record, fail
from ..annotations import digest
from ..api import row
from .schemas import ConfigInput, SkillInput, RunInput, ReplyInput
from .skills import seed_skills, snapshot_skills
from .service import schedule, cancel_run

router = APIRouter(prefix="/api/v1", tags=["Agent"])


def get_run(db, user, run_id, writable=False):
    run = db.get(AgentRun, run_id)
    if not run:
        fail("NOT_FOUND", "Agent 运行不存在。", 404)
    project_access(db, user, run.project_id, ("owner",), writable)
    return run


def info(run):
    return row(run, ("config_snapshot", "request_hash", "idempotency_key"))


@router.get("/admin/agent-configs")
def configs(user=Depends(admin), db=Depends(get_db)):
    return [
        row(x)
        for x in db.scalars(
            select(AgentConfig).order_by(AgentConfig.created_at.desc())
        ).all()
    ]


def validate_config(db, body):
    if not db.get(Provider, body.provider_id):
        fail("NOT_FOUND", "规划服务不存在。", 404)
    try:
        snapshot_skills(db, body.skill_ids)
    except ValueError as error:
        fail("INVALID_SKILLS", str(error))


@router.post("/admin/agent-configs", status_code=201)
def create_config(body: ConfigInput, user=Depends(admin), db=Depends(get_db)):
    validate_config(db, body)
    config = AgentConfig(**body.model_dump())
    db.add(config)
    db.flush()
    record(db, "agent.config_created", user.id, summary={"config_id": config.id})
    db.commit()
    return row(config)


@router.put("/admin/agent-configs/{config_id}")
def edit_config(
    config_id: str, body: ConfigInput, user=Depends(admin), db=Depends(get_db)
):
    config = db.get(AgentConfig, config_id)
    if not config:
        fail("NOT_FOUND", "Agent 配置不存在。", 404)
    validate_config(db, body)
    for key, value in body.model_dump().items():
        setattr(config, key, value)
    record(db, "agent.config_updated", user.id, summary={"config_id": config.id})
    db.commit()
    return row(config)


@router.get("/admin/agent-skills")
def skills(user=Depends(admin), db=Depends(get_db)):
    return [
        row(x) for x in db.scalars(select(AgentSkill).order_by(AgentSkill.name)).all()
    ]


@router.post("/admin/agent-skills/seed")
def seed(user=Depends(admin), db=Depends(get_db)):
    added = seed_skills(db)
    db.commit()
    return {"added": added}


@router.post("/admin/agent-skills", status_code=201)
def create_skill(body: SkillInput, user=Depends(admin), db=Depends(get_db)):
    skill = AgentSkill(**body.model_dump())
    db.add(skill)
    db.commit()
    return row(skill)


@router.put("/admin/agent-skills/{skill_id}")
def edit_skill(
    skill_id: str, body: SkillInput, user=Depends(admin), db=Depends(get_db)
):
    skill = db.get(AgentSkill, skill_id)
    if not skill:
        fail("NOT_FOUND", "技能不存在。", 404)
    for key, value in body.model_dump().items():
        setattr(skill, key, value)
    skill.version += 1
    record(
        db,
        "agent.skill_updated",
        user.id,
        summary={"skill_id": skill.id, "version": skill.version},
    )
    db.commit()
    return row(skill)


@router.get("/projects/{project_id}/agent-configs")
def available_configs(project_id: str, user=Depends(current_user), db=Depends(get_db)):
    project_access(db, user, project_id, ("owner",))
    return [
        {"id": x.id, "name": x.name, "engine": x.engine}
        for x in db.scalars(
            select(AgentConfig)
            .join(Provider, Provider.id == AgentConfig.provider_id)
            .join(ProviderGrant, ProviderGrant.provider_id == Provider.id)
            .where(
                ProviderGrant.project_id == project_id,
                Provider.enabled == True,
                AgentConfig.enabled == True,
            )
        ).all()
    ]


@router.post("/projects/{project_id}/agent-runs", status_code=202)
def create_run(
    project_id: str,
    body: RunInput,
    key: str = Header(alias="Idempotency-Key", min_length=8, max_length=100),
    user=Depends(current_user),
    db=Depends(get_db),
):
    project = project_access(db, user, project_id, ("owner",), True)
    request_hash = digest({"project_id": project.id, "request": body.model_dump()})
    old = db.scalar(
        select(AgentRun).where(
            AgentRun.actor_id == user.id, AgentRun.idempotency_key == key
        )
    )
    if old:
        if old.request_hash != request_hash:
            fail("IDEMPOTENCY_CONFLICT", "相同操作键对应不同指令。", 409)
        return info(old)
    config = db.get(AgentConfig, body.config_id)
    if not config or not config.enabled:
        fail("INVALID_AGENT", "请选择启用的 Agent 配置。")
    provider = db.get(Provider, config.provider_id)
    if (
        not provider
        or not provider.enabled
        or not db.get(ProviderGrant, (config.provider_id, project.id))
    ):
        fail("FORBIDDEN", "项目未获规划服务授权。", 403)
    from ..models import Dataset, ModelSpec

    if body.dataset_id:
        dataset = db.get(Dataset, body.dataset_id)
        if not dataset or dataset.project_id != project.id or dataset.status != "ready":
            fail("INVALID_DATASET", "请选择本项目已发布的数据版本。")
    if body.model_id:
        model = db.get(ModelSpec, body.model_id)
        if not model or model.task_type != project.task_type:
            fail("MODEL_MISMATCH", "所选标注模型能力不匹配。")
        from .tools import model_context

        if body.model_id not in {x["id"] for x in model_context(db, project)}:
            fail("MODEL_UNAVAILABLE", "所选模型未就绪或项目未获授权。")
    try:
        selected_skills = snapshot_skills(db, config.skill_ids)
    except ValueError as error:
        fail("INVALID_SKILLS", str(error))
    run = AgentRun(
        project_id=project.id,
        actor_id=user.id,
        config_id=config.id,
        instruction=body.instruction,
        allowed_actions=list(dict.fromkeys(body.allowed_actions)),
        context={
            "schema_id": project.active_schema_id,
            "dataset_id": body.dataset_id,
            "model_id": body.model_id,
            "max_items": body.max_items,
        },
        config_snapshot=jsonable_encoder({**row(config), "skills": selected_skills}),
        idempotency_key=key,
        request_hash=request_hash,
    )
    db.add(run)
    db.flush()
    schedule(db, run)
    record(db, "agent.created", user.id, project.id, {"run_id": run.id})
    db.commit()
    return info(run)


@router.get("/projects/{project_id}/agent-runs")
def runs(
    project_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
    user=Depends(current_user),
    db=Depends(get_db),
):
    project_access(db, user, project_id, ("owner",))
    return [
        info(x)
        for x in db.scalars(
            select(AgentRun)
            .where(AgentRun.project_id == project_id)
            .order_by(AgentRun.created_at.desc())
            .offset(offset)
            .limit(limit)
        ).all()
    ]


@router.get("/agent-runs/{run_id}")
def detail(run_id: str, user=Depends(current_user), db=Depends(get_db)):
    run = get_run(db, user, run_id)
    calls = db.scalars(
        select(AgentToolCall)
        .where(AgentToolCall.run_id == run.id)
        .order_by(AgentToolCall.created_at, AgentToolCall.step_index)
    ).all()
    return {
        **info(run),
        "tool_calls": [row(x) for x in calls],
        "skills": [
            {
                "id": s["id"],
                "name": s["name"],
                "version": s["version"],
                "hash": s["hash"],
            }
            for s in run.config_snapshot["skills"]
        ],
    }


@router.post("/agent-runs/{run_id}/input", status_code=202)
def reply(
    run_id: str,
    body: ReplyInput,
    key: str = Header(alias="Idempotency-Key", min_length=8, max_length=100),
    user=Depends(current_user),
    db=Depends(get_db),
):
    run = get_run(db, user, run_id, True)
    run = db.scalar(
        select(AgentRun)
        .where(AgentRun.id == run.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    old = next((x for x in run.inputs if x["operation_id"] == key), None)
    if old:
        if old["message"] != body.message:
            fail("IDEMPOTENCY_CONFLICT", "同一补充键对应不同内容。", 409)
        return info(run)
    if run.status != "needs_input":
        fail("INVALID_STATE", "仅等待补充信息时可继续。", 409)
    if (
        len(run.inputs) >= 10
        or sum(len(x["message"]) for x in run.inputs) + len(body.message) > 16000
    ):
        fail("INPUT_LIMIT", "补充信息达到上限，请创建新运行。")
    run.inputs = [*run.inputs, {"operation_id": key, "message": body.message}]
    run.questions = []
    run.plan = None
    run.error = None
    run.summary = {}
    run.turn += 1
    schedule(db, run)
    record(db, "agent.input_received", user.id, run.project_id, {"run_id": run.id})
    db.commit()
    return info(run)


@router.post("/agent-runs/{run_id}/cancel")
def cancel(run_id: str, user=Depends(current_user), db=Depends(get_db)):
    run = get_run(db, user, run_id, True)
    run = db.scalar(
        select(AgentRun)
        .where(AgentRun.id == run.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    cancel_run(db, run)
    db.commit()
    return info(run)
