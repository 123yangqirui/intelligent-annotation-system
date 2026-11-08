"""恢复到明确命名的隔离数据库与文件目录，默认不覆盖主库。"""

import argparse
from pathlib import Path
import subprocess
import tarfile
import shutil
import re
import psycopg
from psycopg import sql

parser = argparse.ArgumentParser()
parser.add_argument("backup")
parser.add_argument("--database", required=True)
parser.add_argument("--objects", required=True)
args = parser.parse_args()
source = Path(args.backup).resolve()
objects = Path(args.objects).resolve()
if not re.fullmatch(r"annotation_restore_[A-Za-z0-9_]+", args.database):
    raise SystemExit("恢复目标库名必须以 annotation_restore_ 开头，避免覆盖主库。")
if objects.exists():
    raise SystemExit("文件目标目录已存在，请选择新目录。")
if not all((source/name).is_file() for name in ('database.dump','objects.tar.gz')):
    raise SystemExit('备份缺少 database.dump 或 objects.tar.gz，尚未创建恢复数据库。')
with psycopg.connect(
    "postgresql://annotation@127.0.0.1:55432/postgres", autocommit=True
) as connection:
    if connection.execute(
        "select 1 from pg_database where datname=%s", (args.database,)
    ).fetchone():
        raise SystemExit("目标数据库已存在，未覆盖。")
    connection.execute(
        sql.SQL("create database {}").format(sql.Identifier(args.database))
    )
subprocess.run(
    [
        "/opt/homebrew/opt/postgresql@17/bin/pg_restore",
        "-h",
        "127.0.0.1",
        "-p",
        "55432",
        "-U",
        "annotation",
        "--no-owner",
        "-d",
        args.database,
        str(source / "database.dump"),
    ],
    check=True,
)
objects.mkdir(parents=True, mode=0o700)
with tarfile.open(source / "objects.tar.gz", "r:gz") as archive:
    archive.extractall(objects, filter="data")
if (source / "secrets.env").exists():
    shutil.copy2(source / "secrets.env", objects / "secrets.env")
    (objects / "secrets.env").chmod(0o600)
print("恢复完成：数据库", args.database, "文件目录", objects)
