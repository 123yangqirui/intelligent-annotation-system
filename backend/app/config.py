"""服务配置。真实凭证由 .env 或部署 secrets 提供。"""

from pathlib import Path
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    app_env: str = "development"
    database_url: str = "postgresql+psycopg://annotation@127.0.0.1:55432/annotation_db"
    redis_broker_url: str = "redis://127.0.0.1:56379/0"
    redis_cache_url: str = "redis://127.0.0.1:56380/0"
    mongodb_uri: str = "mongodb://127.0.0.1:57017"
    mongodb_database: str = "annotation_events"
    storage_root: Path = ROOT / "data"
    storage_backend: str = "local"
    s3_bucket: str = ""
    s3_endpoint: str | None = None
    secret_key: str = ""
    encryption_key: str = ""
    access_minutes: int = 15
    refresh_days: int = 7
    upload_max_bytes: int = 200 * 1024 * 1024
    image_max_pixels: int = 40_000_000
    event_retention_days: int = 90
    job_lease_seconds: int = 120
    cache_ttl_seconds: int = 86400
    provider_allowed_hosts: str = "127.0.0.1,localhost,api.openai.com,api.deepseek.com,dashscope.aliyuncs.com,api.anthropic.com"
    cookie_secure: bool = False
    public_origins: str = "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8080,http://localhost:8080"
    training_python: str = ""
    model_docker_network: str = ""
    model_root: Path = ROOT.parent


@lru_cache
def settings() -> Settings:
    return Settings()
