"""生成 Docker 本地部署密钥，不覆盖已存在配置。"""

from pathlib import Path
import secrets
from cryptography.fernet import Fernet

root = Path(__file__).resolve().parent.parent
path = root / "deploy" / ".env"
if path.exists():
    raise SystemExit("deploy/.env 已存在，未覆盖。")
path.write_text(
    "PROJECT_ROOT="
    + str(root)
    + "\nDATABASE_PASSWORD="
    + secrets.token_urlsafe(24)
    + "\nMONGO_PASSWORD="
    + secrets.token_urlsafe(24)
    + "\nSECRET_KEY="
    + secrets.token_urlsafe(48)
    + "\nENCRYPTION_KEY="
    + Fernet.generate_key().decode()
    + "\nPUBLIC_ORIGINS=http://127.0.0.1:8080,http://localhost:8080\n"
)
path.chmod(0o600)
print("已生成 deploy/.env。")
