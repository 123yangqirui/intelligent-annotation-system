import hashlib
from uuid import uuid4
from pathlib import PurePosixPath
from .config import settings


def safe_key(key):
    p = PurePosixPath(key)
    if p.is_absolute() or ".." in p.parts or "\\" in key:
        raise ValueError("非法文件路径")
    return str(p)


def put(data: bytes, suffix="bin"):
    key = f"objects/{hashlib.sha256(data).hexdigest()}.{suffix}"
    cfg = settings()
    if cfg.storage_backend == "s3":
        import boto3

        boto3.client("s3", endpoint_url=cfg.s3_endpoint).put_object(
            Bucket=cfg.s3_bucket, Key=key, Body=data
        )
    else:
        path = cfg.storage_root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            temp = path.with_suffix("." + str(uuid4()) + ".tmp")
            temp.write_bytes(data)
            temp.replace(path)
    return key


def read(key):
    key = safe_key(key)
    cfg = settings()
    if cfg.storage_backend == "s3":
        import boto3

        body = boto3.client("s3", endpoint_url=cfg.s3_endpoint).get_object(
            Bucket=cfg.s3_bucket, Key=key
        )["Body"]
        try:
            return body.read()
        finally:
            body.close()
    path = (cfg.storage_root / key).resolve()
    if not path.is_relative_to(cfg.storage_root.resolve()):
        raise ValueError("非法文件路径")
    return path.read_bytes()
