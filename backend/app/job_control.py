"""Cooperative cancellation and fencing for long-running task stages."""

from sqlalchemy import select
from .models import Job


class JobCancelled(RuntimeError):
    pass


class LeaseLost(RuntimeError):
    pass


def check_active(db, job, lease=None, lock=False):
    query = select(Job.status, Job.lease_token).where(Job.id == job.id)
    if lock:
        query = query.with_for_update()
    with db.no_autoflush:
        current = db.execute(query).first()
    if not current or (lease is not None and current.lease_token != lease):
        raise LeaseLost("任务执行租约已失效")
    if current.status in ("cancel_requested", "cancelled"):
        raise JobCancelled("任务已取消")
