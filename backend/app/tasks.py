"""持久化任务、事务事件派发与 Celery 执行。"""

import time
import threading
import logging
from datetime import timedelta
from celery import Celery
from sqlalchemy import select, update, func
from .job_control import check_active, JobCancelled, LeaseLost
from .config import settings
from .db import session_scope, new_session
from .models import (
    Job,
    JobItem,
    User,
    LabelSchema,
    ModelSpec,
    Revision,
    Item,
    Outbox,
    Export,
    now,
    uid,
)
from .security import project_access, record, utc
from .annotations import digest

celery_app = Celery("annotation", broker=settings().redis_broker_url)
celery_app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    broker_transport_options={"visibility_timeout": 3600},
    task_ignore_result=True,
)


def create_job(db, user, project_id, kind, config, key, item_ids=None):
    request_hash = digest(
        {"kind": kind, "project_id": project_id, "config": config, "items": item_ids}
    )
    old = db.scalar(
        select(Job).where(Job.actor_id == user.id, Job.idempotency_key == key)
    )
    if old:
        if old.request_hash != request_hash:
            from .security import fail

            fail("IDEMPOTENCY_CONFLICT", "幂等键对应不同任务。", 409)
        return old
    job = Job(
        actor_id=user.id,
        project_id=project_id,
        type=kind,
        config=config,
        idempotency_key=key,
        request_hash=request_hash,
    )
    db.add(job)
    db.flush()
    for item_id in item_ids or []:
        db.add(JobItem(job_id=job.id, item_id=item_id))
    db.add(Outbox(topic="job", payload={"job_id": job.id}))
    record(db, "job.created", user.id, project_id, {"job_id": job.id, "type": kind})
    return job


def counts(db, job):
    result = {
        state: 0 for state in ("pending", "running", "succeeded", "failed", "cancelled")
    }
    result.update(
        dict(
            db.execute(
                select(JobItem.status, func.count())
                .where(JobItem.job_id == job.id)
                .group_by(JobItem.status)
            ).all()
        )
    )
    return result


def ensure_actor(db, job):
    db.expire_all()
    user = db.get(User, job.actor_id)
    if not user or not user.enabled:
        raise ValueError("任务创建者已被禁用")
    if job.project_id:
        return project_access(db, user, job.project_id, ("owner",), writable=True)
    if not user.is_admin:
        raise ValueError("任务需要管理员权限")


@celery_app.task(name="annotation.execute")
def execute_job(job_id):
    db = new_session()
    lease = uid()
    stopped = threading.Event()

    def heartbeat():
        while not stopped.wait(20):
            try:
                with session_scope() as pulse:
                    pulse.execute(
                        update(Job)
                        .where(
                            Job.id == job_id,
                            Job.lease_token == lease,
                            Job.status.in_(["running", "cancel_requested"]),
                        )
                        .values(
                            lease_until=now()
                            + timedelta(seconds=settings().job_lease_seconds)
                        )
                    )
            except Exception:
                continue

    try:
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if not job or job.status in (
            "succeeded",
            "partially_failed",
            "failed",
            "cancelled",
        ):
            return
        if job.status == "cancel_requested":
            job.status = "cancelled"
            db.commit()
            return
        if job.lease_until and utc(job.lease_until) > now():
            return
        job.lease_token = lease
        job.lease_until = now() + timedelta(seconds=settings().job_lease_seconds)
        job.status = "running"
        db.commit()
        threading.Thread(target=heartbeat, daemon=True).start()
        project = ensure_actor(db, job)
        if job.type == "import":
            from .imports import run_import

            run_import(db, project, job, lambda: check_active(db, job, lease))
        elif job.type == "export":
            from .imports import export_data

            export = db.get(Export, job.config["export_id"])
            job.result = export_data(db, export, lambda: check_active(db, job, lease))
            job.status = "succeeded"
        elif job.type == "prediction":
            from .inference import predict

            schema = db.get(LabelSchema, job.config["schema_id"])
            model = db.get(ModelSpec, job.config["model_id"])
            items = db.scalars(
                select(JobItem).where(JobItem.job_id == job.id).order_by(JobItem.id)
            ).all()
            for entry in items:
                db.refresh(job)
                if job.lease_token != lease:
                    return
                if job.status == "cancel_requested":
                    for pending in items:
                        if pending.status in ("pending", "running"):
                            pending.status = "cancelled"
                    job.status = "cancelled"
                    break
                ensure_actor(db, job)
                if entry.status == "succeeded":
                    continue
                job.lease_until = now() + timedelta(
                    seconds=settings().job_lease_seconds
                )
                entry.status = "running"
                entry.attempts += 1
                db.commit()
                try:
                    item = db.get(Item, entry.item_id)
                    content, signals = predict(db, model, job, project, schema, item)
                    db.refresh(job)
                    if job.lease_token != lease:
                        return
                    check_active(db, job, lease)
                    op = "prediction:" + entry.id
                    revision = db.scalar(
                        select(Revision).where(Revision.operation_id == op)
                    )
                    if not revision:
                        score = signals.get("score")
                        if score is None and project.task_type == "image_detection":
                            scores = [
                                x
                                for x in signals.get("box_scores", [])
                                if type(x) in (int, float)
                            ]
                            if scores:
                                score = min(scores)
                        reasons = ["manual_review_required"]
                        if isinstance(score, (int, float)) and score < model.config.get(
                            "review_threshold", 0.8
                        ):
                            reasons.append("low_model_score")
                        if (
                            project.task_type == "image_detection"
                            and not content["boxes"]
                        ):
                            reasons.append("empty_prediction_requires_image_review")
                        revision = Revision(
                            project_id=project.id,
                            item_id=item.id,
                            schema_id=schema.id,
                            source="model",
                            model_id=model.id,
                            operation_id=op,
                            request_hash=digest(content),
                            content=content,
                            signals={**signals, "review_reasons": reasons},
                        )
                        db.add(revision)
                        db.flush()
                    entry.revision_id = revision.id
                    entry.status = "succeeded"
                    record(
                        db,
                        "prediction.saved",
                        job.actor_id,
                        project.id,
                        {"revision_id": revision.id, "signals": signals},
                    )
                    db.commit()
                except (JobCancelled, LeaseLost):
                    raise
                except Exception as error:
                    db.rollback()
                    entry = db.get(JobItem, entry.id)
                    job = db.get(Job, job_id)
                    if job.lease_token != lease:
                        return
                    entry.status = "failed"
                    entry.error = str(getattr(error, "detail", error))[:500]
                    db.commit()
            if job.status != "cancelled":
                tally = counts(db, job)
                job.status = (
                    ("partially_failed" if tally["succeeded"] else "failed")
                    if tally["failed"]
                    else "succeeded"
                )
                job.result = tally
        elif job.type in ("deploy", "stop_deployment"):
            from .runtime import run_deployment

            job.result = run_deployment(db, job)
            job.status = "succeeded"
        elif job.type in ("training", "evaluation"):
            from .training import run_experiment

            job.result = run_experiment(
                db, job, project, lambda: check_active(db, job, lease)
            )
            job.status = "succeeded"
        elif job.type == "agent":
            from .agent.service import run_turn

            job.result = run_turn(
                db, job, project, lambda: check_active(db, job, lease)
            )
            job.status = "succeeded"
        else:
            raise ValueError("不支持的任务类型")
        # 导入/导出等阶段任务同样检查租约；失去租约的进程不能提交。
        check_active(db, job, lease, lock=True)
        job.lease_until = None
        record(
            db,
            "job.finished",
            job.actor_id,
            job.project_id,
            {"job_id": job.id, "status": job.status},
        )
        db.commit()
    except LeaseLost:
        db.rollback()
    except JobCancelled:
        db.rollback()
        job = db.get(Job, job_id, populate_existing=True)
        if job and job.lease_token == lease:
            job.status = "cancelled"
            if job.type == "agent":
                from .agent.service import job_stopped

                job_stopped(db, job, "cancelled")
            job.lease_until = None
            db.execute(
                update(JobItem)
                .where(
                    JobItem.job_id == job_id, JobItem.status.in_(["pending", "running"])
                )
                .values(status="cancelled")
            )
            db.commit()
    except Exception as error:
        db.rollback()
        job = db.get(Job, job_id)
        if job and job.lease_token == lease:
            job.status = "failed"
            job.errors = [{"message": str(getattr(error, "detail", error))[:500]}]
            if job.type == "agent":
                from .agent.service import job_stopped

                job_stopped(db, job, "failed", "Agent 执行失败：" + str(error)[:300])
            job.lease_until = None
            db.commit()
    finally:
        stopped.set()
        db.close()


def dispatch_once():
    from pymongo import MongoClient

    processed = 0
    with session_scope() as db:
        rows = db.scalars(
            select(Outbox)
            .where(Outbox.delivered == False)
            .order_by(Outbox.created_at)
            .limit(100)
            .with_for_update(skip_locked=True)
        ).all()
        for row in rows:
            try:
                if row.topic == "job":
                    job = db.get(Job, row.payload["job_id"])
                    queue = {
                        "training": "training",
                        "deploy": "deployment",
                        "stop_deployment": "deployment",
                        "agent": "agent",
                    }.get(job.type, "annotation")
                    execute_job.apply_async(args=[job.id], queue=queue)
                    job.dispatched_at = now()
                else:
                    with MongoClient(
                        settings().mongodb_uri,
                        serverSelectionTimeoutMS=500,
                        connectTimeoutMS=500,
                    ) as client:
                        collection = client[settings().mongodb_database].events
                        collection.create_index("event_id", unique=True)
                        collection.create_index("expire_at", expireAfterSeconds=0)
                        collection.update_one(
                            {"event_id": row.id},
                            {
                                "$setOnInsert": {
                                    **row.payload,
                                    "event_id": row.id,
                                    "created_at": row.created_at,
                                    "expire_at": row.created_at
                                    + timedelta(days=settings().event_retention_days),
                                }
                            },
                            upsert=True,
                        )
                row.delivered = True
                processed += 1
            except Exception as error:
                logging.getLogger("annotation").warning(
                    "outbox_delivery_failed topic=%s error_type=%s",
                    row.topic,
                    type(error).__name__,
                )
                continue
    return processed


def recover_once():
    from .agent.service import recover_agents

    recover_agents()
    with session_scope() as db:
        rows = db.scalars(
            select(Job).where(Job.status.in_(["queued", "running", "cancel_requested"]))
        ).all()
        for job in rows:
            if (
                job.status == "queued"
                and job.dispatched_at
                and utc(job.dispatched_at) > now() - timedelta(seconds=60)
            ):
                continue
            if (
                job.status == "queued"
                or not job.lease_until
                or utc(job.lease_until) < now()
            ):
                if job.status == "running":
                    job.status = "queued"
                    job.lease_token = None
                    job.lease_until = None
                old = db.scalar(
                    select(Outbox).where(
                        Outbox.topic == "job",
                        Outbox.delivered == False,
                        Outbox.payload["job_id"].as_string() == job.id,
                    )
                )
                if not old:
                    db.add(Outbox(topic="job", payload={"job_id": job.id}))


def dispatcher():
    while True:
        recover_once()
        dispatch_once()
        time.sleep(2)


if __name__ == "__main__":
    dispatcher()
