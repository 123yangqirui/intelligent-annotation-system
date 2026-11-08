"""PostgreSQL 业务模型；SQLite 仅用于隔离测试。"""

from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import (
    Column,
    String,
    Text,
    Integer,
    Boolean,
    DateTime,
    ForeignKey,
    JSON,
    UniqueConstraint,
    CheckConstraint,
    Index,
)
from sqlalchemy.dialects.postgresql import JSONB
from .db import Base


def now():
    return datetime.now(timezone.utc)


def uid():
    return str(uuid4())


J = JSON().with_variant(JSONB(), "postgresql")


class User(Base):
    __tablename__ = "users"
    id = Column(String(36), primary_key=True, default=uid)
    username = Column(String(80), unique=True, nullable=False)
    password_hash = Column(Text, nullable=False)
    enabled = Column(Boolean, default=True, nullable=False)
    is_admin = Column(Boolean, default=False, nullable=False)
    token_version = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)


class RefreshSession(Base):
    __tablename__ = "refresh_sessions"
    id = Column(String(36), primary_key=True, default=uid)
    user_id = Column(ForeignKey("users.id"), nullable=False)
    family_id = Column(String(36), nullable=False)
    token_hash = Column(String(64), unique=True, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked = Column(Boolean, default=False, nullable=False)


class Project(Base):
    __tablename__ = "projects"
    id = Column(String(36), primary_key=True, default=uid)
    name = Column(String(100), nullable=False)
    task_type = Column(String(32), nullable=False)
    description = Column(Text, default="", nullable=False)
    status = Column(String(20), default="active", nullable=False)
    created_by = Column(ForeignKey("users.id"), nullable=False)
    allow_self_review = Column(Boolean, default=False, nullable=False)
    active_schema_id = Column(String(36))
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), default=now, onupdate=now, nullable=False
    )
    __table_args__ = (
        CheckConstraint("task_type IN ('text_classification','image_detection')"),
    )


class Member(Base):
    __tablename__ = "project_members"
    project_id = Column(ForeignKey("projects.id"), primary_key=True)
    user_id = Column(ForeignKey("users.id"), primary_key=True)
    role = Column(String(20), nullable=False)
    __table_args__ = (CheckConstraint("role IN ('owner','annotator','reviewer')"),)


class LabelSchema(Base):
    __tablename__ = "label_schema_versions"
    id = Column(String(36), primary_key=True, default=uid)
    project_id = Column(ForeignKey("projects.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    status = Column(String(20), default="draft", nullable=False)
    labels = Column(J, nullable=False)
    rules = Column(J, default=dict, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)
    __table_args__ = (UniqueConstraint("project_id", "version"),)


class Dataset(Base):
    __tablename__ = "dataset_versions"
    id = Column(String(36), primary_key=True, default=uid)
    project_id = Column(ForeignKey("projects.id"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    status = Column(String(24), default="staging", nullable=False)
    config = Column(J, default=dict, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)


class Item(Base):
    __tablename__ = "data_items"
    id = Column(String(36), primary_key=True, default=uid)
    project_id = Column(ForeignKey("projects.id"), nullable=False, index=True)
    external_id = Column(String(256))
    content_hash = Column(String(64), nullable=False)
    text = Column(Text)
    object_key = Column(Text)
    width = Column(Integer)
    height = Column(Integer)
    metadata_json = Column(J, default=dict, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)
    __table_args__ = (UniqueConstraint("project_id", "content_hash"),)


class DatasetItem(Base):
    __tablename__ = "dataset_version_items"
    dataset_id = Column(ForeignKey("dataset_versions.id"), primary_key=True)
    item_id = Column(ForeignKey("data_items.id"), primary_key=True)
    split = Column(String(12), default="train", nullable=False)


class Reference(Base):
    __tablename__ = "reference_annotations"
    id = Column(String(36), primary_key=True, default=uid)
    item_id = Column(ForeignKey("data_items.id"), nullable=False)
    schema_id = Column(ForeignKey("label_schema_versions.id"), nullable=False)
    content = Column(J, nullable=False)
    __table_args__ = (UniqueConstraint("item_id", "schema_id"),)


class Work(Base):
    __tablename__ = "work_states"
    id = Column(String(36), primary_key=True, default=uid)
    item_id = Column(ForeignKey("data_items.id"), nullable=False)
    schema_id = Column(ForeignKey("label_schema_versions.id"), nullable=False)
    state = Column(String(20), default="unstarted", nullable=False)
    draft_id = Column(String(36))
    submitted_id = Column(String(36))
    accepted_id = Column(String(36))
    version = Column(Integer, default=1, nullable=False)
    claim_user_id = Column(ForeignKey("users.id"))
    claim_expires_at = Column(DateTime(timezone=True))
    __mapper_args__ = {"version_id_col": version}
    __table_args__ = (
        UniqueConstraint("item_id", "schema_id"),
        Index("ix_work_schema_state", "schema_id", "state"),
    )


class Revision(Base):
    __tablename__ = "annotation_revisions"
    id = Column(String(36), primary_key=True, default=uid)
    project_id = Column(ForeignKey("projects.id"), nullable=False)
    item_id = Column(ForeignKey("data_items.id"), nullable=False, index=True)
    schema_id = Column(ForeignKey("label_schema_versions.id"), nullable=False)
    source = Column(String(16), nullable=False)
    actor_id = Column(ForeignKey("users.id"))
    base_id = Column(String(36))
    content = Column(J, nullable=False)
    operation_id = Column(String(128), unique=True, nullable=False)
    request_hash = Column(String(64), nullable=False)
    model_id = Column(String(36))
    signals = Column(J, default=dict, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)
    __table_args__ = (
        Index("ix_revision_item_schema_created", "item_id", "schema_id", "created_at"),
    )


class Review(Base):
    __tablename__ = "review_decisions"
    id = Column(String(36), primary_key=True, default=uid)
    revision_id = Column(ForeignKey("annotation_revisions.id"), nullable=False)
    reviewer_id = Column(ForeignKey("users.id"), nullable=False)
    decision = Column(String(16), nullable=False)
    comment = Column(Text, default="", nullable=False)
    operation_id = Column(String(64), unique=True, nullable=False)
    request_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)


class Provider(Base):
    __tablename__ = "provider_configs"
    id = Column(String(36), primary_key=True, default=uid)
    name = Column(String(100), nullable=False)
    base_url = Column(Text, nullable=False)
    model_name = Column(String(160), nullable=False)
    encrypted_key = Column(Text, nullable=False)
    max_concurrency = Column(Integer, default=2, nullable=False)
    max_tokens = Column(Integer, default=128, nullable=False)
    budget_tokens = Column(Integer, default=100000, nullable=False)
    spent_tokens = Column(Integer, default=0, nullable=False)
    reserved_tokens = Column(Integer, default=0, nullable=False)
    enabled = Column(Boolean, default=True, nullable=False)


class ProviderGrant(Base):
    __tablename__ = "provider_project_grants"
    provider_id = Column(ForeignKey("provider_configs.id"), primary_key=True)
    project_id = Column(ForeignKey("projects.id"), primary_key=True)


class ModelSpec(Base):
    __tablename__ = "model_specs"
    id = Column(String(36), primary_key=True, default=uid)
    name = Column(String(100), nullable=False)
    adapter = Column(String(32), nullable=False)
    task_type = Column(String(32), nullable=False)
    provider_id = Column(ForeignKey("provider_configs.id"))
    config = Column(J, default=dict, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)


class Deployment(Base):
    __tablename__ = "model_deployments"
    id = Column(String(36), primary_key=True, default=uid)
    model_id = Column(ForeignKey("model_specs.id"), nullable=False)
    status = Column(String(24), default="requested", nullable=False)
    config = Column(J, nullable=False)
    runtime_id = Column(String(160))
    error = Column(Text)
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)


class Job(Base):
    __tablename__ = "jobs"
    id = Column(String(36), primary_key=True, default=uid)
    project_id = Column(ForeignKey("projects.id"))
    actor_id = Column(ForeignKey("users.id"), nullable=False)
    type = Column(String(24), nullable=False)
    status = Column(String(24), default="queued", nullable=False, index=True)
    config = Column(J, nullable=False)
    idempotency_key = Column(String(100), nullable=False)
    request_hash = Column(String(64), nullable=False)
    parent_id = Column(String(36))
    result = Column(J)
    errors = Column(J, default=list, nullable=False)
    lease_token = Column(String(36))
    lease_until = Column(DateTime(timezone=True))
    dispatched_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)
    __table_args__ = (UniqueConstraint("actor_id", "idempotency_key"),)


class JobItem(Base):
    __tablename__ = "job_items"
    id = Column(String(36), primary_key=True, default=uid)
    job_id = Column(ForeignKey("jobs.id"), nullable=False, index=True)
    item_id = Column(ForeignKey("data_items.id"), nullable=False)
    status = Column(String(20), default="pending", nullable=False)
    revision_id = Column(String(36))
    error = Column(Text)
    attempts = Column(Integer, default=0, nullable=False)
    __table_args__ = (UniqueConstraint("job_id", "item_id"),)


class ModelCall(Base):
    __tablename__ = "model_calls"
    id = Column(String(36), primary_key=True, default=uid)
    job_id = Column(ForeignKey("jobs.id"), nullable=False)
    provider_id = Column(ForeignKey("provider_configs.id"), nullable=False)
    status = Column(String(24), default="reserved", nullable=False)
    reserved = Column(Integer, nullable=False)
    usage = Column(J)
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)


class Outbox(Base):
    __tablename__ = "outbox_events"
    id = Column(String(36), primary_key=True, default=uid)
    topic = Column(String(40), nullable=False)
    payload = Column(J, nullable=False)
    delivered = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)
    __table_args__ = (Index("ix_outbox_pending_created", "delivered", "created_at"),)


class Audit(Base):
    __tablename__ = "audit_logs"
    id = Column(String(36), primary_key=True, default=uid)
    actor_id = Column(String(36))
    project_id = Column(String(36), index=True)
    action = Column(String(80), nullable=False)
    summary = Column(J, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)


class Export(Base):
    __tablename__ = "exports"
    id = Column(String(36), primary_key=True, default=uid)
    project_id = Column(ForeignKey("projects.id"), nullable=False)
    schema_id = Column(ForeignKey("label_schema_versions.id"), nullable=False)
    snapshot = Column(J, nullable=False)
    format = Column(String(16), nullable=False)
    object_key = Column(Text)
    job_id = Column(ForeignKey("jobs.id"))
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)


class AgentConfig(Base):
    __tablename__ = "agent_configs"
    id = Column(String(36), primary_key=True, default=uid)
    name = Column(String(100), nullable=False)
    engine = Column(String(24), nullable=False)
    protocol = Column(String(24), nullable=False, default="anthropic")
    provider_id = Column(ForeignKey("provider_configs.id"), nullable=False)
    system_prompt = Column(Text, nullable=False, default="")
    skill_ids = Column(J, nullable=False, default=list)
    enabled = Column(Boolean, nullable=False, default=True)
    max_steps = Column(Integer, nullable=False, default=8)
    max_turns = Column(Integer, nullable=False, default=6)
    max_output_tokens = Column(Integer, nullable=False, default=2048)
    timeout_seconds = Column(Integer, nullable=False, default=90)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)


class AgentSkill(Base):
    __tablename__ = "agent_skills"
    id = Column(String(36), primary_key=True, default=uid)
    name = Column(String(64), unique=True, nullable=False)
    description = Column(String(500), nullable=False)
    content = Column(Text, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    version = Column(Integer, nullable=False, default=1)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=now, onupdate=now
    )


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id = Column(String(36), primary_key=True, default=uid)
    project_id = Column(ForeignKey("projects.id"), nullable=False)
    actor_id = Column(ForeignKey("users.id"), nullable=False)
    config_id = Column(ForeignKey("agent_configs.id"), nullable=False)
    instruction = Column(Text, nullable=False)
    inputs = Column(J, nullable=False, default=list)
    context = Column(J, nullable=False, default=dict)
    config_snapshot = Column(J, nullable=False)
    allowed_actions = Column(J, nullable=False, default=list)
    status = Column(String(24), nullable=False, default="queued")
    plan = Column(J)
    questions = Column(J, nullable=False, default=list)
    summary = Column(J, nullable=False, default=dict)
    error = Column(Text)
    active_job_id = Column(String(36))
    planning_call_id = Column(String(36))
    turn = Column(Integer, nullable=False, default=1)
    planning_attempts = Column(Integer, nullable=False, default=0)
    idempotency_key = Column(String(100), nullable=False)
    request_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=now, onupdate=now
    )
    __table_args__ = (
        UniqueConstraint("actor_id", "idempotency_key"),
        Index("ix_agent_project_created", "project_id", "created_at"),
        Index("ix_agent_status", "status"),
    )


class AgentToolCall(Base):
    __tablename__ = "agent_tool_calls"
    id = Column(String(36), primary_key=True, default=uid)
    run_id = Column(ForeignKey("agent_runs.id"), nullable=False, index=True)
    phase = Column(String(16), nullable=False, default="execution")
    step_index = Column(Integer, nullable=False)
    name = Column(String(64), nullable=False)
    arguments = Column(J, nullable=False, default=dict)
    status = Column(String(24), nullable=False, default="pending")
    result = Column(J, nullable=False, default=dict)
    child_job_id = Column(String(36))
    error = Column(Text)
    duration_ms = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
    __table_args__ = (UniqueConstraint("run_id", "phase", "step_index"),)
