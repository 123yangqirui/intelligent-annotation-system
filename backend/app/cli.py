import argparse
import getpass
import secrets
from cryptography.fernet import Fernet
from .config import ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=[
            "configure",
            "create-admin",
            "seed-demo",
            "dispatch",
            "recover",
            "seed-agent-skills",
        ],
    )
    parser.add_argument("--username", default="admin")
    args = parser.parse_args()
    if args.action == "configure":
        path = ROOT / ".env"
        if path.exists():
            print("已有 .env，未覆盖。")
            return
        path.write_text(
            "SECRET_KEY="
            + secrets.token_urlsafe(48)
            + "\nENCRYPTION_KEY="
            + Fernet.generate_key().decode()
            + "\n"
        )
        path.chmod(0o600)
        print("已生成本地配置密钥；请勿提交 .env。")
        return
    from .db import session_scope
    from .models import User
    from .security import passwords
    from sqlalchemy import select

    if args.action == "dispatch":
        from .tasks import dispatcher

        dispatcher()
        return
    if args.action == "recover":
        from .tasks import recover_once

        recover_once()
        return
    if args.action == "seed-agent-skills":
        from .agent.skills import seed_skills

        with session_scope() as db:
            added = seed_skills(db)
        print("已初始化技能：", added)
        return
    if args.action == "create-admin":
        password = getpass.getpass("管理员密码（至少 8 位）：")
        if len(password) < 8:
            parser.error("密码至少 8 位")
    else:
        path = ROOT.parent / ".local" / "admin-password"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            password = path.read_text().strip()
        else:
            password = secrets.token_urlsafe(18)
            path.write_text(password + "\n")
            path.chmod(0o600)
    with session_scope() as db:
        if db.scalar(select(User).where(User.username == args.username)):
            print("账号已存在，未改密码。")
            return
        db.add(
            User(
                username=args.username,
                password_hash=passwords.hash(password),
                is_admin=True,
            )
        )
    print(
        "管理员已创建。"
        + (
            " 本地密码保存在 .local/admin-password。"
            if args.action == "seed-demo"
            else ""
        )
    )


if __name__ == "__main__":
    main()
