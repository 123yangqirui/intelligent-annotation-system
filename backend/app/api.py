import hashlib
import json
from datetime import timedelta
from fastapi import (
    APIRouter,
    Depends,
    Request,
    Response,
    Cookie,
    UploadFile,
    File,
    Form,
    Header,
    Query,
)
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, update, text
from .db import get_db
from .config import settings
from .models import *
from .schemas import *
from .security import (
    current_user,
    admin,
    project_access,
    passwords,
    access_token,
    new_refresh,
    user_info,
    fail,
    crypt,
    record,
    utc,
)
from .annotations import (
    validate_labels,
    validate_content,
    get_work,
    digest,
    same_operation,
)
from .tasks import create_job, counts
from . import storage

router = APIRouter(prefix="/api/v1")


def row(obj, exclude=()):
    return {
        column.name: getattr(obj, column.name)
        for column in obj.__table__.columns
        if column.name not in exclude
    }


def csrf(request):
    origin = request.headers.get("origin")
    allowed = {x.strip() for x in settings().public_origins.split(",")} | {
        str(request.base_url).rstrip("/")
    }
    if origin and origin not in allowed:
        fail("CSRF_REJECTED", "不允许的请求来源。", 403)


def set_refresh(response, token):
    response.set_cookie(
        "refresh_token",
        token,
        httponly=True,
        secure=settings().cookie_secure,
        samesite="strict",
        path="/api/v1/auth",
        max_age=settings().refresh_days * 86400,
    )


@router.post("/auth/login")
def login(body: LoginInput, response: Response, request: Request, db=Depends(get_db)):
    csrf(request)
    from .limits import login_limit

    identity = (
        (request.client.host if request.client else "unknown") + ":" + body.username
    )
    login_limit(identity)
    user = db.scalar(select(User).where(User.username == body.username))
    valid = False
    if user:
        try:
            valid = passwords.verify(body.password, user.password_hash)
        except Exception:
            pass
    if not user or not user.enabled or not valid:
        login_limit(identity, failed=True)
        fail("INVALID_CREDENTIALS", "用户名或密码错误。", 401)
    token = new_refresh(db, user)
    set_refresh(response, token)
    record(db, "auth.login", user.id)
    db.commit()
    return {"access_token": access_token(user), "user": user_info(user)}


@router.post("/auth/password")
def change_password(
    body: PasswordInput, user=Depends(current_user), db=Depends(get_db)
):
    if not passwords.verify(body.current_password, user.password_hash):
        fail("INVALID_CREDENTIALS", "当前密码错误。", 401)
    user.password_hash = passwords.hash(body.new_password)
    user.token_version += 1
    db.execute(
        update(RefreshSession)
        .where(RefreshSession.user_id == user.id)
        .values(revoked=True)
    )
    record(db, "auth.password_changed", user.id)
    db.commit()
    return {"ok": True}


@router.post("/auth/refresh")
def refresh(
    request: Request,
    response: Response,
    refresh_token: str | None = Cookie(default=None),
    db=Depends(get_db),
):
    csrf(request)
    if not refresh_token:
        fail("UNAUTHORIZED", "没有可刷新会话。", 401)
    session = db.scalar(
        select(RefreshSession)
        .where(
            RefreshSession.token_hash
            == hashlib.sha256(refresh_token.encode()).hexdigest()
        )
        .with_for_update()
    )
    if not session:
        fail("UNAUTHORIZED", "会话不存在。", 401)
    if session.revoked:
        db.execute(
            update(RefreshSession)
            .where(RefreshSession.family_id == session.family_id)
            .values(revoked=True)
        )
        db.commit()
        fail("UNAUTHORIZED", "旧刷新凭证已使用，会话族已撤销。", 401)
    user = db.get(User, session.user_id)
    if utc(session.expires_at) < now() or not user or not user.enabled:
        fail("UNAUTHORIZED", "会话失效。", 401)
    session.revoked = True
    token = new_refresh(db, user, session.family_id)
    db.commit()
    set_refresh(response, token)
    return {"access_token": access_token(user), "user": user_info(user)}


@router.post("/auth/logout")
def logout(
    request: Request,
    response: Response,
    refresh_token: str | None = Cookie(default=None),
    db=Depends(get_db),
):
    csrf(request)
    if refresh_token:
        session = db.scalar(
            select(RefreshSession).where(
                RefreshSession.token_hash
                == hashlib.sha256(refresh_token.encode()).hexdigest()
            )
        )
        if session:
            db.execute(
                update(RefreshSession)
                .where(RefreshSession.family_id == session.family_id)
                .values(revoked=True)
            )
            user = db.get(User, session.user_id)
            user.token_version += 1
    db.commit()
    response.delete_cookie("refresh_token", path="/api/v1/auth")
    return {"ok": True}


@router.get("/auth/me")
def me(user=Depends(current_user)):
    return user_info(user)


@router.get("/admin/users")
def users(user=Depends(admin), db=Depends(get_db)):
    return [
        user_info(x) for x in db.scalars(select(User).order_by(User.created_at)).all()
    ]


@router.post("/admin/users", status_code=201)
def add_user(body: UserInput, user=Depends(admin), db=Depends(get_db)):
    if db.scalar(select(User).where(User.username == body.username)):
        fail("USER_EXISTS", "用户名已存在。", 409)
    result = User(
        username=body.username,
        password_hash=passwords.hash(body.password),
        is_admin=body.is_admin,
    )
    db.add(result)
    db.flush()
    record(db, "user.created", user.id, summary={"user_id": result.id})
    db.commit()
    return user_info(result)


@router.patch("/admin/users/{user_id}")
def disable_user(user_id: str, enabled: bool, user=Depends(admin), db=Depends(get_db)):
    target = db.get(User, user_id)
    if not target:
        fail("NOT_FOUND", "用户不存在。", 404)
    if user_id == user.id and not enabled:
        fail("SELF_DISABLE", "不能禁用当前管理员。")
    target.enabled = enabled
    target.token_version += 1
    db.execute(
        update(RefreshSession)
        .where(RefreshSession.user_id == user_id)
        .values(revoked=True)
    )
    record(
        db,
        "user.enabled_changed",
        user.id,
        summary={"user_id": user_id, "enabled": enabled},
    )
    db.commit()
    return user_info(target)


@router.get("/health/db")
def database_health(user=Depends(current_user), db=Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"code": 200, "status": "ok", "database": "connected"}


@router.get("/projects")
def projects(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    user=Depends(current_user),
    db=Depends(get_db),
):
    query = select(Project)
    if not user.is_admin:
        query = query.join(Member, Member.project_id == Project.id).where(
            Member.user_id == user.id
        )
    return [
        row(x)
        for x in db.scalars(
            query.order_by(Project.created_at.desc()).offset(offset).limit(limit)
        ).all()
    ]


@router.post("/projects", status_code=201)
def add_project(body: ProjectInput, user=Depends(current_user), db=Depends(get_db)):
    project = Project(**body.model_dump(), created_by=user.id)
    db.add(project)
    db.flush()
    db.add(Member(project_id=project.id, user_id=user.id, role="owner"))
    record(db, "project.created", user.id, project.id)
    db.commit()
    return row(project)


@router.get("/projects/{project_id}")
def project_detail(project_id: str, user=Depends(current_user), db=Depends(get_db)):
    project = project_access(db, user, project_id)
    data = row(project)
    member = db.get(Member, (project_id, user.id))
    data["role"] = "admin" if user.is_admin else member.role
    data["item_count"] = db.scalar(
        select(func.count()).select_from(Item).where(Item.project_id == project_id)
    )
    data["accepted_count"] = db.scalar(
        select(func.count())
        .select_from(Work)
        .join(Item, Item.id == Work.item_id)
        .where(
            Item.project_id == project_id,
            Work.schema_id == project.active_schema_id,
            Work.state == "accepted",
        )
    )
    data["work_counts"] = {
        state: 0
        for state in ("unstarted", "draft", "submitted", "accepted", "returned")
    }
    data["work_counts"].update(
        dict(
            db.execute(
                select(Work.state, func.count())
                .join(Item, Item.id == Work.item_id)
                .where(
                    Item.project_id == project_id,
                    Work.schema_id == project.active_schema_id,
                )
                .group_by(Work.state)
            ).all()
        )
    )
    return data


@router.patch("/projects/{project_id}")
def edit_project(
    project_id: str, body: ProjectInput, user=Depends(current_user), db=Depends(get_db)
):
    project = project_access(db, user, project_id, ("owner",), True)
    if body.task_type != project.task_type:
        fail("TASK_IMMUTABLE", "项目任务类型不能修改。")
    project.name = body.name
    project.description = body.description
    project.allow_self_review = body.allow_self_review
    record(db, "project.updated", user.id, project.id)
    db.commit()
    return row(project)


@router.post("/projects/{project_id}/archive")
def archive_project(project_id: str, user=Depends(current_user), db=Depends(get_db)):
    project = project_access(db, user, project_id, ("owner",), True)
    project.status = "archived"
    record(db, "project.archived", user.id, project_id)
    db.commit()
    return row(project)


@router.get("/projects/{project_id}/members")
def members(project_id: str, user=Depends(current_user), db=Depends(get_db)):
    project_access(db, user, project_id, ("owner",))
    return [
        {**user_info(u), "role": m.role}
        for m, u in db.execute(
            select(Member, User)
            .join(User, Member.user_id == User.id)
            .where(Member.project_id == project_id)
        )
    ]


@router.post("/projects/{project_id}/members")
def add_member(
    project_id: str,
    user_id: str,
    role: Literal["owner", "annotator", "reviewer"],
    user=Depends(current_user),
    db=Depends(get_db),
):
    project_access(db, user, project_id, ("owner",), True)
    db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if not db.get(User, user_id):
        fail("NOT_FOUND", "用户不存在。", 404)
    member = db.get(Member, (project_id, user_id))
    if not member:
        member = Member(project_id=project_id, user_id=user_id, role=role)
        db.add(member)
    else:
        if (
            member.role == "owner"
            and role != "owner"
            and db.scalar(
                select(func.count())
                .select_from(Member)
                .where(Member.project_id == project_id, Member.role == "owner")
            )
            <= 1
        ):
            fail("LAST_OWNER", "须保留至少一名项目负责人。")
        member.role = role
    record(
        db, "member.changed", user.id, project_id, {"user_id": user_id, "role": role}
    )
    db.commit()
    return {"ok": True}


@router.delete("/projects/{project_id}/members/{user_id}")
def remove_member(
    project_id: str, user_id: str, user=Depends(current_user), db=Depends(get_db)
):
    project_access(db, user, project_id, ("owner",), True)
    db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    member = db.get(Member, (project_id, user_id))
    if not member:
        fail("NOT_FOUND", "成员不存在。", 404)
    if (
        member.role == "owner"
        and db.scalar(
            select(func.count())
            .select_from(Member)
            .where(Member.project_id == project_id, Member.role == "owner")
        )
        <= 1
    ):
        fail("LAST_OWNER", "须保留至少一名项目负责人。")
    db.delete(member)
    record(db, "member.removed", user.id, project_id, {"user_id": user_id})
    db.commit()
    return {"ok": True}


@router.get("/projects/{project_id}/schemas")
def schemas(project_id: str, user=Depends(current_user), db=Depends(get_db)):
    project_access(db, user, project_id)
    return [
        row(x)
        for x in db.scalars(
            select(LabelSchema)
            .where(LabelSchema.project_id == project_id)
            .order_by(LabelSchema.version.desc())
        ).all()
    ]


@router.post("/projects/{project_id}/schemas", status_code=201)
def add_schema(
    project_id: str, body: SchemaInput, user=Depends(current_user), db=Depends(get_db)
):
    project_access(db, user, project_id, ("owner",), True)
    db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    version = (
        db.scalar(
            select(func.max(LabelSchema.version)).where(
                LabelSchema.project_id == project_id
            )
        )
        or 0
    ) + 1
    previous = db.scalar(
        select(LabelSchema)
        .where(LabelSchema.project_id == project_id)
        .order_by(LabelSchema.version.desc())
        .limit(1)
    )
    stable = {x["name"]: x["id"] for x in previous.labels} if previous else {}
    labels = [
        {**x, "id": x.get("id") or stable.get(str(x.get("name", "")).strip())}
        for x in body.labels
    ]
    schema = LabelSchema(
        project_id=project_id,
        version=version,
        labels=validate_labels(labels),
        rules=body.rules,
    )
    db.add(schema)
    db.flush()
    record(db, "schema.created", user.id, project_id, {"schema_id": schema.id})
    db.commit()
    return row(schema)


@router.post("/schemas/{schema_id}/publish")
def publish_schema(schema_id: str, user=Depends(current_user), db=Depends(get_db)):
    schema = db.get(LabelSchema, schema_id)
    if not schema:
        fail("NOT_FOUND", "规范不存在。", 404)
    project = project_access(db, user, schema.project_id, ("owner",), True)
    schema.status = "published"
    project.active_schema_id = schema.id
    for item in db.scalars(select(Item).where(Item.project_id == project.id)).all():
        get_work(db, item.id, schema.id)
    record(db, "schema.published", user.id, project.id, {"schema_id": schema.id})
    db.commit()
    return row(schema)


@router.put("/schemas/{schema_id}")
def edit_schema(
    schema_id: str, body: SchemaInput, user=Depends(current_user), db=Depends(get_db)
):
    schema = db.get(LabelSchema, schema_id)
    if not schema:
        fail("NOT_FOUND", "规范不存在。", 404)
    project_access(db, user, schema.project_id, ("owner",), True)
    if schema.status != "draft":
        fail("SCHEMA_IMMUTABLE", "已发布规范不可修改，请创建新版本。", 409)
    schema.labels = validate_labels(body.labels)
    schema.rules = body.rules
    db.commit()
    return row(schema)


@router.get("/projects/{project_id}/datasets")
def datasets(project_id: str, user=Depends(current_user), db=Depends(get_db)):
    project_access(db, user, project_id)
    return [
        row(x)
        for x in db.scalars(
            select(Dataset)
            .where(Dataset.project_id == project_id)
            .order_by(Dataset.created_at.desc())
        ).all()
    ]


@router.post("/projects/{project_id}/datasets")
def new_dataset(
    project_id: str,
    name: str = Query("手工数据集", min_length=1, max_length=100),
    user=Depends(current_user),
    db=Depends(get_db),
):
    project_access(db, user, project_id, ("owner",), True)
    result = Dataset(project_id=project_id, name=name, status="staging", config={})
    db.add(result)
    db.commit()
    return row(result)


@router.post("/datasets/{dataset_id}/publish")
def publish_dataset(dataset_id: str, user=Depends(current_user), db=Depends(get_db)):
    dataset = db.get(Dataset, dataset_id)
    if not dataset:
        fail("NOT_FOUND", "数据版本不存在。", 404)
    project_access(db, user, dataset.project_id, ("owner",), True)
    if dataset.status not in ("staging", "needs_resolution"):
        fail("INVALID_STATE", "只有待发布版本可以发布。", 409)
    if not db.scalar(
        select(func.count())
        .select_from(DatasetItem)
        .where(DatasetItem.dataset_id == dataset_id)
    ):
        fail("EMPTY_DATASET", "空数据版本不能发布。")
    dataset.status = "ready"
    db.commit()
    return row(dataset)


@router.post("/datasets/{dataset_id}/fork")
def fork_dataset(dataset_id: str, user=Depends(current_user), db=Depends(get_db)):
    source = db.get(Dataset, dataset_id)
    if not source:
        fail("NOT_FOUND", "数据版本不存在。", 404)
    project_access(db, user, source.project_id, ("owner",), True)
    result = Dataset(
        project_id=source.project_id,
        name=(source.name + " · 新版本")[:100],
        config={**source.config, "base_version_id": source.id},
    )
    db.add(result)
    db.flush()
    for member in db.scalars(
        select(DatasetItem).where(DatasetItem.dataset_id == source.id)
    ).all():
        db.add(
            DatasetItem(
                dataset_id=result.id, item_id=member.item_id, split=member.split
            )
        )
    db.commit()
    return row(result)


@router.post("/datasets/{dataset_id}/split")
def split_dataset(
    dataset_id: str,
    val_fraction: float = Query(0.2, ge=0, lt=1),
    test_fraction: float = Query(0.1, ge=0, lt=1),
    seed: int = 42,
    user=Depends(current_user),
    db=Depends(get_db),
):
    import random

    dataset = db.get(Dataset, dataset_id)
    if not dataset:
        fail("NOT_FOUND", "数据版本不存在。", 404)
    project_access(db, user, dataset.project_id, ("owner",), True)
    if dataset.status != "staging" or val_fraction + test_fraction >= 1:
        fail("INVALID_SPLIT", "划分需要未发布版本，比例总和须小于 1。")
    members = db.scalars(
        select(DatasetItem)
        .where(DatasetItem.dataset_id == dataset_id)
        .order_by(DatasetItem.item_id)
    ).all()
    random.Random(seed).shuffle(members)
    n = len(members)
    nv = int(n * val_fraction)
    nt = int(n * test_fraction)
    for i, member in enumerate(members):
        member.split = "val" if i < nv else "test" if i < nv + nt else "train"
    dataset.config = {
        **dataset.config,
        "split_seed": seed,
        "val_fraction": val_fraction,
        "test_fraction": test_fraction,
    }
    db.commit()
    return {"train": n - nv - nt, "val": nv, "test": nt}


@router.post("/projects/{project_id}/texts")
def add_text(
    project_id: str, body: TextInput, user=Depends(current_user), db=Depends(get_db)
):
    from .imports import add_item

    project = project_access(db, user, project_id, ("owner",), True)
    if project.task_type != "text_classification":
        fail("TASK_MISMATCH", "项目不是文本分类。")
    dataset = db.get(Dataset, body.dataset_id)
    if not dataset or dataset.project_id != project_id or dataset.status != "staging":
        fail("INVALID_DATASET", "请使用本项目未发布的数据版本。")
    result = add_item(db, project, dataset, body.text, body.external_id)
    if project.active_schema_id:
        get_work(db, result.id, project.active_schema_id)
    db.commit()
    return row(result)


@router.post("/projects/{project_id}/imports", status_code=202)
def import_file(
    project_id: str,
    file: UploadFile = File(),
    text_field: str = Form("text"),
    labels_purpose: Literal["none", "seed", "evaluation"] = Form("none"),
    label_mapping: str = Form("{}"),
    split: Literal["train", "val", "test"] = Form("train"),
    schema_id: str | None = Form(None),
    source_url: str = Form(""),
    key: str = Header(alias="Idempotency-Key", min_length=8, max_length=100),
    user=Depends(current_user),
    db=Depends(get_db),
):
    project_access(db, user, project_id, ("owner",), True)
    data = file.file.read(settings().upload_max_bytes + 1)
    if len(data) > settings().upload_max_bytes:
        fail("TOO_LARGE", "上传文件过大。", 413)
    try:
        mapping = json.loads(label_mapping)
    except ValueError:
        fail("INVALID_MAPPING", "标签映射必须是 JSON 对象。")
    if not isinstance(mapping, dict):
        fail("INVALID_MAPPING", "标签映射必须是对象。")
    # 先核验幂等上传，避免重试产生额外数据版本。
    upload_hash = digest(
        {
            "data": hashlib.sha256(data).hexdigest(),
            "project": project_id,
            "text_field": text_field,
            "purpose": labels_purpose,
            "mapping": mapping,
            "split": split,
            "schema": schema_id,
        }
    )
    old = db.scalar(
        select(Job).where(Job.actor_id == user.id, Job.idempotency_key == key)
    )
    if old:
        if old.config.get("upload_hash") != upload_hash:
            fail("IDEMPOTENCY_CONFLICT", "上传重试内容不同。", 409)
        return job_info(db, old)
    if labels_purpose != "none":
        schema = db.get(LabelSchema, schema_id)
        if (
            not schema
            or schema.project_id != project_id
            or schema.status != "published"
        ):
            fail("SCHEMA_MISMATCH", "请选择本项目已发布规范。")
    dataset = Dataset(
        project_id=project_id,
        name=(file.filename or "import")[:100],
        config={"split": split},
    )
    db.add(dataset)
    db.flush()
    config = {
        "dataset_id": dataset.id,
        "object_key": storage.put(data, "upload"),
        "filename": file.filename or "upload",
        "text_field": text_field,
        "labels_purpose": labels_purpose,
        "label_mapping": mapping,
        "schema_id": schema_id,
        "upload_hash": upload_hash,
        "source_url": source_url[:2000],
    }
    job = create_job(db, user, project_id, "import", config, key)
    db.commit()
    return job_info(db, job)


@router.get("/projects/{project_id}/items")
def items(
    project_id: str,
    dataset_id: str | None = None,
    state: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    user=Depends(current_user),
    db=Depends(get_db),
):
    project = project_access(db, user, project_id)
    query = select(Item).where(Item.project_id == project_id)
    if dataset_id:
        query = query.join(DatasetItem, DatasetItem.item_id == Item.id).where(
            DatasetItem.dataset_id == dataset_id
        )
    if state:
        if state not in ("unstarted", "draft", "submitted", "accepted", "returned"):
            fail("INVALID_STATE", "标注筛选状态无效。")
        query = query.join(Work, Work.item_id == Item.id).where(
            Work.schema_id == project.active_schema_id, Work.state == state
        )
    page = db.scalars(
        query.order_by(Item.created_at, Item.id).offset(offset).limit(limit)
    ).all()
    states = {
        work.item_id: work
        for work in db.scalars(
            select(Work).where(
                Work.item_id.in_([item.id for item in page]),
                Work.schema_id == project.active_schema_id,
            )
        ).all()
    }
    results = [
        {**row(item), "work": row(states[item.id]) if item.id in states else None}
        for item in page
    ]
    return results


@router.get("/items/{item_id}")
def item_detail(
    item_id: str,
    schema_id: str | None = None,
    user=Depends(current_user),
    db=Depends(get_db),
):
    item = db.get(Item, item_id)
    if not item:
        fail("NOT_FOUND", "数据不存在。", 404)
    project = project_access(db, user, item.project_id)
    schema_id = schema_id or project.active_schema_id
    schema = db.get(LabelSchema, schema_id) if schema_id else None
    if schema_id and (
        not schema or schema.project_id != project.id or schema.status != "published"
    ):
        fail("SCHEMA_MISMATCH", "请选择本项目已发布规范。")
    work = get_work(db, item.id, schema.id) if schema else None
    revisions = db.scalars(
        select(Revision)
        .where(Revision.item_id == item_id, Revision.schema_id == schema_id)
        .order_by(Revision.created_at.desc())
    ).all()
    reviews = [
        {**row(review), "reviewer_name": reviewer.username}
        for review, reviewer in db.execute(
            select(Review, User)
            .join(User, User.id == Review.reviewer_id)
            .join(Revision, Revision.id == Review.revision_id)
            .where(Revision.item_id == item_id, Revision.schema_id == schema_id)
            .order_by(Review.created_at.desc())
        )
    ]
    db.commit()
    return {
        **row(item),
        "schema": row(schema) if schema else None,
        "work": row(work) if work else None,
        "revisions": [row(x) for x in revisions],
        "reviews": reviews,
    }


def item_schema(db, item, schema_id):
    schema = db.get(LabelSchema, schema_id)
    if (
        not schema
        or schema.project_id != item.project_id
        or schema.status != "published"
    ):
        fail("SCHEMA_MISMATCH", "请选择数据所属项目已发布规范。")
    return schema


def ensure_claim(work, user):
    if (
        work.claim_user_id
        and work.claim_user_id != user.id
        and work.claim_expires_at
        and utc(work.claim_expires_at) > now()
    ):
        fail("CLAIMED", "数据正在由其他标注员编辑，请稍后重试。", 409)


def set_claim(db, work, user_id, expires_at):
    # 领取与续约不修改标注版本，避免心跳使自己的草稿产生冲突。
    db.execute(
        update(Work)
        .where(Work.id == work.id)
        .values(claim_user_id=user_id, claim_expires_at=expires_at)
    )
    db.refresh(work)


@router.get("/items/{item_id}/file")
def item_file(item_id: str, user=Depends(current_user), db=Depends(get_db)):
    import io

    item = db.get(Item, item_id)
    if not item:
        fail("NOT_FOUND", "图片不存在。", 404)
    project_access(db, user, item.project_id)
    if not item.object_key:
        fail("NOT_FOUND", "数据没有图片。", 404)
    return StreamingResponse(
        io.BytesIO(storage.read(item.object_key)), media_type="image/png"
    )


@router.post("/projects/{project_id}/claims/next")
def claim_next(
    project_id: str,
    exclude_item_id: str | None = None,
    user=Depends(current_user),
    db=Depends(get_db),
):
    project = project_access(db, user, project_id, ("owner", "annotator"), True)
    if not project.active_schema_id:
        fail("SCHEMA_REQUIRED", "请先发布规范。")
    from sqlalchemy import or_

    query = (
        select(Work)
        .join(Item, Item.id == Work.item_id)
        .where(
            Item.project_id == project_id,
            Work.schema_id == project.active_schema_id,
            Work.state.in_(["unstarted", "draft", "returned"]),
            or_(
                Work.claim_user_id == None,
                Work.claim_expires_at < now(),
                Work.claim_user_id == user.id,
            ),
        )
        .with_for_update(skip_locked=True)
    )
    if exclude_item_id:
        query = query.where(Work.item_id != exclude_item_id)
    work = db.scalar(query.order_by(Work.id).limit(1))
    if not work:
        return {"item_id": None}
    set_claim(db, work, user.id, now() + timedelta(minutes=5))
    db.commit()
    return {"item_id": work.item_id, "expires_at": work.claim_expires_at}


@router.post("/items/{item_id}/revisions")
def save_revision(
    item_id: str, body: RevisionInput, user=Depends(current_user), db=Depends(get_db)
):
    item = db.get(Item, item_id)
    if not item:
        fail("NOT_FOUND", "数据不存在。", 404)
    project = project_access(
        db, user, item.project_id, ("owner", "annotator", "reviewer"), True
    )
    schema = db.get(LabelSchema, body.schema_id)
    if not schema:
        fail("SCHEMA_MISMATCH", "规范不存在。")
    request_hash = digest({"item": item_id, "request": body.model_dump()})
    old = same_operation(db, Revision, body.operation_id, request_hash, user.id)
    if old:
        return {
            "revision": row(old),
            "work": row(get_work(db, item_id, body.schema_id)),
        }
    content = validate_content(project, schema, item, body.content)
    if body.base_id:
        base = db.get(Revision, body.base_id)
        if not base or base.item_id != item_id or base.schema_id != schema.id:
            fail("INVALID_BASE", "来源修订不匹配。")
    work = get_work(db, item_id, schema.id, True)
    ensure_claim(work, user)
    if work.version != body.expected_version:
        fail("REVISION_CONFLICT", "标注已更新，请重新加载。", 409)
    member = db.get(Member, (project.id, user.id))
    if not user.is_admin and member.role == "reviewer" and work.state != "submitted":
        fail("FORBIDDEN", "审核员只能修正已提交标注。", 403)
    revision = Revision(
        project_id=project.id,
        item_id=item_id,
        schema_id=schema.id,
        source="human",
        actor_id=user.id,
        base_id=body.base_id,
        content=content,
        operation_id=body.operation_id,
        request_hash=request_hash,
    )
    db.add(revision)
    db.flush()
    work.draft_id = revision.id
    work.submitted_id = None
    work.accepted_id = None
    work.state = "draft"
    record(db, "annotation.saved", user.id, project.id, {"revision_id": revision.id})
    db.commit()
    return {"revision": row(revision), "work": row(work)}


@router.post("/items/{item_id}/claim")
def renew_claim(
    item_id: str, schema_id: str, user=Depends(current_user), db=Depends(get_db)
):
    item = db.get(Item, item_id)
    if not item:
        fail("NOT_FOUND", "数据不存在。", 404)
    project_access(db, user, item.project_id, ("owner", "annotator"), True)
    item_schema(db, item, schema_id)
    work = get_work(db, item_id, schema_id, True)
    ensure_claim(work, user)
    set_claim(db, work, user.id, now() + timedelta(minutes=5))
    db.commit()
    return {"expires_at": work.claim_expires_at, "version": work.version}


@router.delete("/items/{item_id}/claim")
def release_claim(
    item_id: str, schema_id: str, user=Depends(current_user), db=Depends(get_db)
):
    item = db.get(Item, item_id)
    if not item:
        fail("NOT_FOUND", "数据不存在。", 404)
    project_access(db, user, item.project_id)
    item_schema(db, item, schema_id)
    work = get_work(db, item_id, schema_id, True)
    if work.claim_user_id != user.id:
        fail("FORBIDDEN", "不是当前领取者。", 403)
    set_claim(db, work, None, None)
    db.commit()
    return {"ok": True}


@router.post("/revisions/{revision_id}/correction")
def correct_and_review(
    revision_id: str,
    body: RevisionInput,
    comment: str = "",
    user=Depends(current_user),
    db=Depends(get_db),
):
    original = db.get(Revision, revision_id)
    if not original:
        fail("NOT_FOUND", "修订不存在。", 404)
    project = project_access(db, user, original.project_id, ("owner", "reviewer"), True)
    if original.actor_id == user.id and not project.allow_self_review:
        fail("SELF_REVIEW", "不能审核自己的原始提交。", 403)
    if body.schema_id != original.schema_id:
        fail("SCHEMA_MISMATCH", "修正规范不匹配。")
    request_hash = digest(
        {"revision": revision_id, "request": body.model_dump(), "comment": comment}
    )
    old = same_operation(db, Revision, body.operation_id, request_hash, user.id)
    if old:
        return row(old)
    work = get_work(db, original.item_id, original.schema_id, True)
    if (
        work.state != "submitted"
        or work.submitted_id != revision_id
        or work.version != body.expected_version
    ):
        fail("REVISION_CONFLICT", "当前提交已更新。", 409)
    content = validate_content(
        project,
        db.get(LabelSchema, original.schema_id),
        db.get(Item, original.item_id),
        body.content,
    )
    revision = Revision(
        project_id=project.id,
        item_id=original.item_id,
        schema_id=original.schema_id,
        source="human",
        actor_id=user.id,
        base_id=original.id,
        content=content,
        operation_id=body.operation_id,
        request_hash=request_hash,
    )
    db.add(revision)
    db.flush()
    db.add(
        Review(
            revision_id=revision.id,
            reviewer_id=user.id,
            decision="accept",
            comment=comment,
            operation_id="correction:" + digest(body.operation_id)[:40],
            request_hash=request_hash,
        )
    )
    work.state = "accepted"
    work.draft_id = revision.id
    work.accepted_id = revision.id
    work.submitted_id = None
    record(
        db,
        "review.corrected",
        user.id,
        project.id,
        {"original_id": original.id, "revision_id": revision.id},
    )
    db.commit()
    return row(revision)


@router.post("/items/{item_id}/submit")
def submit(
    item_id: str, body: SubmitInput, user=Depends(current_user), db=Depends(get_db)
):
    item = db.get(Item, item_id)
    if not item:
        fail("NOT_FOUND", "数据不存在。", 404)
    project = project_access(
        db, user, item.project_id, ("owner", "annotator", "reviewer"), True
    )
    item_schema(db, item, body.schema_id)
    work = get_work(db, item_id, body.schema_id, True)
    ensure_claim(work, user)
    if work.submitted_id == body.revision_id and work.state == "submitted":
        return row(work)
    revision = db.get(Revision, body.revision_id)
    if (
        work.version != body.expected_version
        or work.draft_id != body.revision_id
        or not revision
        or revision.actor_id != user.id
    ):
        fail("REVISION_CONFLICT", "提交版本不匹配或非本人修订。", 409)
    work.state = "submitted"
    work.submitted_id = revision.id
    work.claim_user_id = None
    work.claim_expires_at = None
    record(
        db, "annotation.submitted", user.id, project.id, {"revision_id": revision.id}
    )
    db.commit()
    return row(work)


@router.get("/projects/{project_id}/reviews")
def review_list(
    project_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    user=Depends(current_user),
    db=Depends(get_db),
):
    project = project_access(db, user, project_id, ("owner", "reviewer"))
    return [
        {**row(w), "text": i.text, "external_id": i.external_id}
        for w, i in db.execute(
            select(Work, Item)
            .join(Item, Item.id == Work.item_id)
            .where(
                Item.project_id == project_id,
                Work.schema_id == project.active_schema_id,
                Work.state == "submitted",
            )
            .order_by(Item.created_at, Item.id)
            .offset(offset)
            .limit(limit)
        )
    ]


@router.post("/revisions/{revision_id}/reviews")
def review_revision(
    revision_id: str, body: ReviewInput, user=Depends(current_user), db=Depends(get_db)
):
    revision = db.get(Revision, revision_id)
    if not revision:
        fail("NOT_FOUND", "修订不存在。", 404)
    project = project_access(db, user, revision.project_id, ("owner", "reviewer"), True)
    request_hash = digest({"revision_id": revision_id, "request": body.model_dump()})
    old = same_operation(db, Review, body.operation_id, request_hash, user.id)
    if old:
        return row(old)
    if revision.actor_id == user.id and not project.allow_self_review:
        fail("SELF_REVIEW", "当前项目不允许审核自己的标注。", 403)
    work = get_work(db, revision.item_id, revision.schema_id, True)
    if (
        work.version != body.expected_version
        or work.submitted_id != revision.id
        or work.state != "submitted"
    ):
        fail("REVISION_CONFLICT", "此提交已过期。", 409)
    review = Review(
        revision_id=revision.id,
        reviewer_id=user.id,
        decision=body.decision,
        comment=body.comment,
        operation_id=body.operation_id,
        request_hash=request_hash,
    )
    db.add(review)
    work.state = "accepted" if body.decision == "accept" else "returned"
    work.accepted_id = revision.id if body.decision == "accept" else None
    work.submitted_id = None
    record(
        db,
        "review.decided",
        user.id,
        project.id,
        {"revision_id": revision.id, "decision": body.decision},
    )
    db.commit()
    return row(review)


def job_info(db, job):
    return {**row(job), "counts": counts(db, job)}


@router.get("/projects/{project_id}/jobs")
def jobs(project_id: str, user=Depends(current_user), db=Depends(get_db)):
    project_access(db, user, project_id)
    return [
        job_info(db, x)
        for x in db.scalars(
            select(Job)
            .where(Job.project_id == project_id)
            .order_by(Job.created_at.desc())
            .limit(100)
        ).all()
    ]


@router.get("/jobs/{job_id}")
def job_detail(job_id: str, user=Depends(current_user), db=Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        fail("NOT_FOUND", "任务不存在。", 404)
    if job.project_id:
        project_access(db, user, job.project_id)
    elif not user.is_admin:
        fail("NOT_FOUND", "任务不存在。", 404)
    return {
        **job_info(db, job),
        "items": [
            row(x)
            for x in db.scalars(select(JobItem).where(JobItem.job_id == job_id)).all()
        ],
    }


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, user=Depends(current_user), db=Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        fail("NOT_FOUND", "任务不存在。", 404)
    if job.project_id:
        project_access(db, user, job.project_id, ("owner",), True)
    elif not user.is_admin:
        fail("FORBIDDEN", "需要管理员。", 403)
    if job.type == "agent":
        from .agent.service import cancel_run

        run = db.get(AgentRun, job.config["run_id"])
        if run:
            cancel_run(db, run)
        db.commit()
        return job_info(db, job)
    if job.status == "queued":
        job.status = "cancelled"
        for entry in db.scalars(select(JobItem).where(JobItem.job_id == job.id)).all():
            entry.status = "cancelled"
    elif job.status == "running":
        job.status = "cancel_requested"
    db.commit()
    return job_info(db, job)


@router.post("/jobs/{job_id}/retry", status_code=202)
def retry_job(
    job_id: str,
    key: str = Header(alias="Idempotency-Key", min_length=8, max_length=100),
    user=Depends(current_user),
    db=Depends(get_db),
):
    parent = db.get(Job, job_id)
    if not parent:
        fail("NOT_FOUND", "任务不存在。", 404)
    if parent.project_id:
        project_access(db, user, parent.project_id, ("owner",), True)
    elif not user.is_admin:
        fail("FORBIDDEN", "需要管理员。", 403)
    if parent.status not in ("failed", "partially_failed", "cancelled"):
        fail("INVALID_STATE", "只有失败或取消任务可重试。", 409)
    if parent.type == "agent":
        fail(
            "AGENT_RETRY_REQUIRED",
            "请在 Agent 助手中创建新运行；后台阶段任务不能独立重试。",
            409,
        )
    failed = db.scalars(
        select(JobItem.item_id).where(
            JobItem.job_id == job_id,
            JobItem.status.in_(["failed", "cancelled", "pending", "running"]),
        )
    ).all()
    child = create_job(
        db, user, parent.project_id, parent.type, parent.config, key, list(failed)
    )
    child.parent_id = parent.id
    db.commit()
    return job_info(db, child)


@router.post("/projects/{project_id}/prediction-jobs", status_code=202)
def prediction_job(
    project_id: str,
    body: PredictionInput,
    key: str = Header(alias="Idempotency-Key", min_length=8, max_length=100),
    user=Depends(current_user),
    db=Depends(get_db),
):
    project = project_access(db, user, project_id, ("owner",), True)
    dataset = db.get(Dataset, body.dataset_id)
    schema = db.get(LabelSchema, body.schema_id)
    model = db.get(ModelSpec, body.model_id)
    if not dataset or dataset.project_id != project_id or dataset.status != "ready":
        fail("INVALID_DATASET", "请选择已发布数据版本。")
    if not schema or schema.project_id != project_id or schema.status != "published":
        fail("SCHEMA_MISMATCH", "请选择已发布规范。")
    if not model or model.task_type != project.task_type:
        fail("MODEL_MISMATCH", "模型任务能力不匹配。")
    if model.provider_id and not db.get(ProviderGrant, (model.provider_id, project_id)):
        fail("FORBIDDEN", "项目未获模型授权。", 403)
    from .inference import ensure_model_available

    ensure_model_available(db, model, project)
    ids = db.scalars(
        select(DatasetItem.item_id).where(DatasetItem.dataset_id == dataset.id)
    ).all()
    if body.item_ids is not None:
        if not set(body.item_ids) <= set(ids):
            fail("INVALID_ITEMS", "样本不属于数据版本。")
        ids = body.item_ids
    ids = sorted(set(ids))
    job = create_job(
        db, user, project_id, "prediction", body.model_dump(), key, list(ids)
    )
    db.commit()
    return job_info(db, job)


@router.get("/models")
def models(user=Depends(current_user), db=Depends(get_db)):
    accessible = []
    for model in db.scalars(select(ModelSpec)).all():
        if model.provider_id and not user.is_admin:
            grants = db.scalars(
                select(ProviderGrant.project_id).where(
                    ProviderGrant.provider_id == model.provider_id
                )
            ).all()
            if not any(db.get(Member, (pid, user.id)) for pid in grants):
                continue
        accessible.append(row(model))
    return accessible


@router.get("/projects/{project_id}/models")
def project_models(project_id: str, user=Depends(current_user), db=Depends(get_db)):
    project = project_access(db, user, project_id)
    from .agent.tools import model_context

    return model_context(db, project)


@router.post("/admin/models", status_code=201)
def add_model(body: ModelInput, user=Depends(admin), db=Depends(get_db)):
    from .inference import validate_url

    if body.adapter == "llm":
        if body.task_type != "text_classification" or not db.get(
            Provider, body.provider_id
        ):
            fail("MODEL_MISMATCH", "语言模型须选择文本任务和供应商。")
    else:
        if (body.adapter == "bert" and body.task_type != "text_classification") or (
            body.adapter == "yolo" and body.task_type != "image_detection"
        ):
            fail("MODEL_MISMATCH", "适配器与任务能力不匹配。")
        if body.provider_id:
            fail("MODEL_MISMATCH", "非语言模型不能绑定大模型供应商。")
        validate_url(body.config.get("endpoint", ""))
    model = ModelSpec(**body.model_dump())
    db.add(model)
    db.commit()
    return row(model)


@router.get("/admin/providers")
def providers(user=Depends(admin), db=Depends(get_db)):
    return [
        {**row(x, ("encrypted_key",)), "has_key": True}
        for x in db.scalars(select(Provider)).all()
    ]


@router.post("/admin/providers", status_code=201)
def add_provider(body: ProviderInput, user=Depends(admin), db=Depends(get_db)):
    from .inference import validate_url

    data = body.model_dump()
    key = data.pop("api_key")
    data["base_url"] = validate_url(data["base_url"])
    provider = Provider(**data, encrypted_key=crypt().encrypt(key.encode()).decode())
    db.add(provider)
    db.flush()
    record(db, "provider.created", user.id, summary={"provider_id": provider.id})
    db.commit()
    return row(provider, ("encrypted_key",))


@router.post("/admin/providers/{provider_id}/grant")
def grant_provider(
    provider_id: str, project_id: str, user=Depends(admin), db=Depends(get_db)
):
    if not db.get(Provider, provider_id) or not db.get(Project, project_id):
        fail("NOT_FOUND", "服务或项目不存在。", 404)
    if not db.get(ProviderGrant, (provider_id, project_id)):
        db.add(ProviderGrant(provider_id=provider_id, project_id=project_id))
    db.commit()
    return {"ok": True}


@router.patch("/admin/providers/{provider_id}")
def update_provider(
    provider_id: str, body: ProviderUpdate, user=Depends(admin), db=Depends(get_db)
):
    provider = db.scalar(
        select(Provider).where(Provider.id == provider_id).with_for_update()
    )
    if not provider:
        fail("NOT_FOUND", "服务不存在。", 404)
    if body.budget_tokens is not None:
        if body.budget_tokens < provider.spent_tokens + provider.reserved_tokens:
            fail("INVALID_BUDGET", "预算不能低于已用和已预留用量。")
        provider.budget_tokens = body.budget_tokens
    if body.max_concurrency is not None:
        provider.max_concurrency = body.max_concurrency
    if body.enabled is not None:
        provider.enabled = body.enabled
    record(db, "provider.updated", user.id, summary={"provider_id": provider.id})
    db.commit()
    return row(provider, ("encrypted_key",))


@router.post("/admin/model-calls/{call_id}/reconcile")
def reconcile_call(
    call_id: str,
    total_tokens: int = Query(ge=0),
    user=Depends(admin),
    db=Depends(get_db),
):
    call = db.scalar(select(ModelCall).where(ModelCall.id == call_id).with_for_update())
    if not call:
        fail("NOT_FOUND", "调用不存在。", 404)
    if call.status != "usage_unknown":
        fail("INVALID_STATE", "仅未知用量调用可对账。", 409)
    provider = db.scalar(
        select(Provider).where(Provider.id == call.provider_id).with_for_update()
    )
    provider.reserved_tokens -= call.reserved
    provider.spent_tokens += total_tokens
    call.status = "reconciled"
    call.usage = {"total_tokens": total_tokens, "reconciled_by": user.id}
    record(
        db,
        "model_call.reconciled",
        user.id,
        summary={"call_id": call_id, "total_tokens": total_tokens},
    )
    db.commit()
    return row(call)


@router.get("/admin/model-calls")
def model_calls(user=Depends(admin), db=Depends(get_db)):
    return [
        row(x)
        for x in db.scalars(
            select(ModelCall).order_by(ModelCall.created_at.desc()).limit(100)
        ).all()
    ]


@router.post("/admin/providers/{provider_id}/rotate")
def rotate_provider(
    provider_id: str, body: KeyInput, user=Depends(admin), db=Depends(get_db)
):
    provider = db.get(Provider, provider_id)
    if not provider:
        fail("NOT_FOUND", "服务不存在。", 404)
    provider.encrypted_key = crypt().encrypt(body.api_key.encode()).decode()
    record(db, "provider.key_rotated", user.id, summary={"provider_id": provider_id})
    db.commit()
    return {"ok": True}


@router.post("/admin/providers/{provider_id}/test")
def provider_test(provider_id: str, user=Depends(admin), db=Depends(get_db)):
    from .inference import validate_url
    import httpx

    provider = db.get(Provider, provider_id)
    if not provider:
        fail("NOT_FOUND", "服务不存在。", 404)
    response = httpx.get(
        validate_url(provider.base_url) + "/models",
        headers={
            "Authorization": "Bearer "
            + crypt().decrypt(provider.encrypted_key.encode()).decode()
        },
        timeout=10,
        follow_redirects=False,
    )
    return {"ok": response.is_success, "http_status": response.status_code}


@router.get("/projects/{project_id}/usage")
def usage(project_id: str, user=Depends(current_user), db=Depends(get_db)):
    project_access(db, user, project_id, ("owner",))
    calls = db.scalars(
        select(ModelCall)
        .join(Job, Job.id == ModelCall.job_id)
        .where(Job.project_id == project_id)
    ).all()
    return [row(x) for x in calls]


@router.get("/projects/{project_id}/exports")
def exports(project_id: str, user=Depends(current_user), db=Depends(get_db)):
    project_access(db, user, project_id, ("owner",))
    return [
        row(x, ("snapshot",))
        for x in db.scalars(
            select(Export)
            .where(Export.project_id == project_id)
            .order_by(Export.created_at.desc())
        ).all()
    ]


@router.post("/projects/{project_id}/exports", status_code=202)
def add_export(
    project_id: str,
    body: ExportInput,
    key: str = Header(alias="Idempotency-Key", min_length=8, max_length=100),
    user=Depends(current_user),
    db=Depends(get_db),
):
    project = project_access(db, user, project_id, ("owner",), True)
    dataset = db.get(Dataset, body.dataset_id)
    schema = db.get(LabelSchema, body.schema_id)
    if (
        not dataset
        or dataset.project_id != project_id
        or dataset.status != "ready"
        or not schema
        or schema.project_id != project_id
    ):
        fail("INVALID_SNAPSHOT", "请选择本项目已发布的数据和规范。")
    if (project.task_type == "text_classification") != (
        body.format in ("jsonl", "csv")
    ):
        fail("FORMAT_MISMATCH", "导出格式不匹配任务。")
    old = db.scalar(
        select(Job).where(Job.actor_id == user.id, Job.idempotency_key == key)
    )
    if old:
        if old.config.get("request") != body.model_dump():
            fail("IDEMPOTENCY_CONFLICT", "导出参数不同。", 409)
        return job_info(db, old)
    snapshot = [
        {"item_id": w.item_id, "revision_id": w.accepted_id, "split": m.split}
        for w, m in db.execute(
            select(Work, DatasetItem)
            .join(DatasetItem, DatasetItem.item_id == Work.item_id)
            .where(
                DatasetItem.dataset_id == dataset.id,
                Work.schema_id == schema.id,
                Work.state == "accepted",
                Work.accepted_id != None,
            )
        )
    ]
    if not snapshot:
        fail("EMPTY_EXPORT", "此版本没有已审核数据。")
    export = Export(
        project_id=project_id,
        schema_id=schema.id,
        snapshot=snapshot,
        format=body.format,
    )
    db.add(export)
    db.flush()
    job = create_job(
        db,
        user,
        project_id,
        "export",
        {"export_id": export.id, "request": body.model_dump()},
        key,
    )
    export.job_id = job.id
    db.commit()
    return job_info(db, job)


@router.get("/exports/{export_id}/download")
def download_export(export_id: str, user=Depends(current_user), db=Depends(get_db)):
    import io

    export = db.get(Export, export_id)
    if not export:
        fail("NOT_FOUND", "导出不存在。", 404)
    project_access(db, user, export.project_id, ("owner",))
    if not export.object_key:
        fail("NOT_READY", "导出尚未准备好。", 409)
    return StreamingResponse(
        io.BytesIO(storage.read(export.object_key)),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="annotations-{export.id}.zip"'
        },
    )


@router.get("/admin/deployments")
def deployments(user=Depends(admin), db=Depends(get_db)):
    return [
        row(x)
        for x in db.scalars(
            select(Deployment).order_by(Deployment.created_at.desc())
        ).all()
    ]


@router.post("/admin/deployments", status_code=202)
def add_deployment(
    body: DeploymentInput,
    key: str = Header(alias="Idempotency-Key", min_length=8, max_length=100),
    user=Depends(admin),
    db=Depends(get_db),
):
    from .runtime import validate_deployment

    model = db.get(ModelSpec, body.model_id)
    if not model:
        fail("NOT_FOUND", "模型不存在。", 404)
    validate_deployment(body.model_dump(), model)
    old = db.scalar(
        select(Job).where(Job.actor_id == user.id, Job.idempotency_key == key)
    )
    if old:
        if old.config.get("request") != body.model_dump():
            fail("IDEMPOTENCY_CONFLICT", "部署参数不同。", 409)
        return job_info(db, old)
    # 每个检查点产生独立规格，避免部署不同权重却复用旧模型版本。
    deployed_spec = ModelSpec(
        name=(model.name + " · " + body.checkpoint)[:100],
        adapter=model.adapter,
        task_type=model.task_type,
        provider_id=model.provider_id,
        config={**model.config, "checkpoint": body.checkpoint, "candidate": False},
    )
    db.add(deployed_spec)
    db.flush()
    deployment = Deployment(model_id=deployed_spec.id, config=body.model_dump())
    db.add(deployment)
    db.flush()
    job = create_job(
        db,
        user,
        None,
        "deploy",
        {"deployment_id": deployment.id, "request": body.model_dump()},
        key,
    )
    db.commit()
    return job_info(db, job)


@router.post("/admin/deployments/{deployment_id}/stop", status_code=202)
def stop_deployment(
    deployment_id: str,
    key: str = Header(alias="Idempotency-Key", min_length=8, max_length=100),
    user=Depends(admin),
    db=Depends(get_db),
):
    if not db.get(Deployment, deployment_id):
        fail("NOT_FOUND", "部署不存在。", 404)
    job = create_job(
        db, user, None, "stop_deployment", {"deployment_id": deployment_id}, key
    )
    db.commit()
    return job_info(db, job)


@router.post("/projects/{project_id}/training-runs", status_code=202)
def experiment(
    project_id: str,
    body: ExperimentInput,
    key: str = Header(alias="Idempotency-Key", min_length=8, max_length=100),
    user=Depends(current_user),
    db=Depends(get_db),
):
    project = project_access(db, user, project_id, ("owner",), True)
    dataset = db.get(Dataset, body.dataset_id)
    schema = db.get(LabelSchema, body.schema_id)
    if (
        not dataset
        or dataset.project_id != project_id
        or dataset.status != "ready"
        or not schema
        or schema.project_id != project_id
    ):
        fail("INVALID_SNAPSHOT", "数据或规范不匹配。")
    expected = "bert" if project.task_type == "text_classification" else "yolo"
    if body.recipe != expected:
        fail("INVALID_RECIPE", "训练配方不匹配任务。")
    snapshot = [
        {"item_id": w.item_id, "revision_id": w.accepted_id, "split": m.split}
        for w, m in db.execute(
            select(Work, DatasetItem)
            .join(DatasetItem, DatasetItem.item_id == Work.item_id)
            .where(
                DatasetItem.dataset_id == dataset.id,
                Work.schema_id == schema.id,
                Work.state == "accepted",
            )
        )
    ]
    if not snapshot:
        fail("EMPTY_TRAINING", "训练需要已审核数据。")
    config = {**body.model_dump(), "snapshot": snapshot}
    job = create_job(db, user, project_id, "training", config, key)
    db.commit()
    return job_info(db, job)


@router.post("/projects/{project_id}/evaluations", status_code=202)
def evaluation(
    project_id: str,
    body: PredictionInput,
    key: str = Header(alias="Idempotency-Key", min_length=8, max_length=100),
    user=Depends(current_user),
    db=Depends(get_db),
):
    project = project_access(db, user, project_id, ("owner",), True)
    dataset = db.get(Dataset, body.dataset_id)
    schema = db.get(LabelSchema, body.schema_id)
    model = db.get(ModelSpec, body.model_id)
    if (
        not dataset
        or dataset.project_id != project_id
        or dataset.status != "ready"
        or not schema
        or schema.project_id != project_id
        or schema.status != "published"
        or not model
    ):
        fail("INVALID_EVALUATION", "请选择已发布的数据版本和规范以及有效模型。")
    if model.task_type != project.task_type:
        fail("MODEL_MISMATCH", "模型任务类型不匹配。")
    if model.provider_id and not db.get(ProviderGrant, (model.provider_id, project_id)):
        fail("FORBIDDEN", "项目未获模型授权。", 403)
    from .inference import ensure_model_available

    ensure_model_available(db, model, project)
    ids = db.scalars(
        select(DatasetItem.item_id).where(DatasetItem.dataset_id == dataset.id)
    ).all()
    if body.item_ids is not None:
        if not set(body.item_ids) <= set(ids):
            fail("INVALID_ITEMS", "样本不属于数据版本。")
        ids = sorted(set(body.item_ids))
    job = create_job(
        db, user, project_id, "evaluation", body.model_dump(), key, list(ids)
    )
    db.commit()
    return job_info(db, job)


@router.get("/jobs/{job_id}/log")
def training_log(job_id: str, user=Depends(current_user), db=Depends(get_db)):
    from fastapi.responses import PlainTextResponse

    job = db.get(Job, job_id)
    if not job or job.type != "training":
        fail("NOT_FOUND", "训练任务不存在。", 404)
    project_access(db, user, job.project_id, ("owner",))
    path = settings().storage_root / "training" / job.id / "training.log"
    if not path.exists():
        return PlainTextResponse("训练尚未生成日志。")
    return PlainTextResponse(path.read_text(errors="replace")[-100000:])


@router.get("/projects/{project_id}/audit")
def audit(project_id: str, user=Depends(current_user), db=Depends(get_db)):
    project_access(db, user, project_id, ("owner",))
    return [
        row(x)
        for x in db.scalars(
            select(Audit)
            .where(Audit.project_id == project_id)
            .order_by(Audit.created_at.desc())
            .limit(200)
        ).all()
    ]


@router.get("/admin/events")
def events(user=Depends(admin)):
    from pymongo import MongoClient

    try:
        with MongoClient(
            settings().mongodb_uri, serverSelectionTimeoutMS=1000
        ) as client:
            return list(
                client[settings().mongodb_database]
                .events.find({}, {"_id": 0})
                .sort("created_at", -1)
                .limit(100)
            )
    except Exception:
        fail("MONGO_UNAVAILABLE", "MongoDB 未连接，必要审计仍保存在 PostgreSQL。", 503)


@router.get("/admin/system")
def system_status(user=Depends(admin), db=Depends(get_db)):
    import shutil
    from redis import Redis
    from pymongo import MongoClient

    status = {}
    for name, url in [
        ("redis_broker", settings().redis_broker_url),
        ("redis_cache", settings().redis_cache_url),
    ]:
        try:
            status[name] = bool(
                Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1).ping()
            )
        except Exception:
            status[name] = False
    try:
        with MongoClient(
            settings().mongodb_uri, serverSelectionTimeoutMS=1000
        ) as client:
            status["mongodb"] = bool(client.admin.command("ping")["ok"])
    except Exception:
        status["mongodb"] = False
    db.execute(text("SELECT 1"))
    status["postgresql"] = True
    status["docker_available"] = bool(shutil.which("docker"))
    status["outbox_pending"] = db.scalar(
        select(func.count()).select_from(Outbox).where(Outbox.delivered == False)
    )
    status["jobs_queued"] = db.scalar(
        select(func.count()).select_from(Job).where(Job.status == "queued")
    )
    return status
