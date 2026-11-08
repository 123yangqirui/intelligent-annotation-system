from datetime import timedelta, timezone
from hashlib import sha256
import secrets
import jwt
from cryptography.fernet import Fernet
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pwdlib import PasswordHash
from .config import settings
from .db import get_db
from .models import User, Project, Member, RefreshSession, Audit, Outbox, now, uid

passwords = PasswordHash.recommended()
bearer = HTTPBearer(auto_error=False)


def fail(code, message, status=400):
    raise HTTPException(status, {"code": code, "message": message})


def utc(dt):
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def user_info(user):
    return {
        "id": user.id,
        "username": user.username,
        "enabled": user.enabled,
        "is_admin": user.is_admin,
    }


def access_token(user):
    cfg = settings()
    if not cfg.secret_key:
        fail("CONFIG_MISSING", "请先执行初始化命令配置签名密钥。", 503)
    return jwt.encode(
        {
            "sub": user.id,
            "ver": user.token_version,
            "exp": now() + timedelta(minutes=cfg.access_minutes),
            "iss": "annotation",
            "aud": "annotation-web",
        },
        cfg.secret_key,
        algorithm="HS256",
    )


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db=Depends(get_db),
):
    if credentials is None:
        fail("UNAUTHORIZED", "请先登录。", 401)
    try:
        data = jwt.decode(
            credentials.credentials,
            settings().secret_key,
            algorithms=["HS256"],
            issuer="annotation",
            audience="annotation-web",
        )
        user = db.get(User, data["sub"])
        if not user or not user.enabled or data["ver"] != user.token_version:
            raise ValueError()
    except (jwt.InvalidTokenError, ValueError, KeyError):
        fail("UNAUTHORIZED", "会话已失效，请重新登录。", 401)
    return user


def admin(user=Depends(current_user)):
    if not user.is_admin:
        fail("FORBIDDEN", "需要管理员权限。", 403)
    return user


def project_access(db, user, project_id, roles=None, writable=False):
    project = db.get(Project, project_id)
    member = db.get(Member, (project_id, user.id))
    if not project or (not user.is_admin and not member):
        fail("NOT_FOUND", "项目不存在。", 404)
    if roles and not user.is_admin and member.role not in roles:
        fail("FORBIDDEN", "没有此操作权限。", 403)
    if writable and project.status != "active":
        fail("PROJECT_ARCHIVED", "项目已归档。", 409)
    return project


def new_refresh(db, user, family=None):
    token = secrets.token_urlsafe(40)
    db.add(
        RefreshSession(
            user_id=user.id,
            family_id=family or uid(),
            token_hash=sha256(token.encode()).hexdigest(),
            expires_at=now() + timedelta(days=settings().refresh_days),
        )
    )
    return token


def crypt():
    try:
        return Fernet(settings().encryption_key.encode())
    except ValueError:
        fail("CONFIG_MISSING", "未配置模型密钥加密主密钥。", 503)


def record(db, action, actor_id=None, project_id=None, summary=None):
    data = summary or {}
    event = Audit(action=action, actor_id=actor_id, project_id=project_id, summary=data)
    db.add(event)
    db.add(
        Outbox(
            topic="audit",
            payload={
                "action": action,
                "actor_id": actor_id,
                "project_id": project_id,
                "summary": data,
            },
        )
    )
