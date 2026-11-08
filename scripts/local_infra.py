"""本机开发基础设施，不创建登录自启服务。"""

import argparse
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT / ".local"
PG = Path("/opt/homebrew/opt/postgresql@17/bin")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "stop"])
    args = parser.parse_args()
    LOCAL.mkdir(exist_ok=True)
    data = LOCAL / "postgres"
    if not (PG / "pg_ctl").exists():
        raise SystemExit(
            "请先安装 PostgreSQL 17 和 Redis：brew install postgresql@17 redis"
        )
    if args.action == "start":
        if not data.exists():
            subprocess.run(
                [
                    str(PG / "initdb"),
                    "-D",
                    str(data),
                    "-U",
                    "annotation",
                    "-A",
                    "trust",
                    "--encoding=UTF8",
                ],
                check=True,
            )
        if subprocess.run(
            [str(PG / "pg_ctl"), "-D", str(data), "status"], capture_output=True
        ).returncode:
            subprocess.run(
                [
                    str(PG / "pg_ctl"),
                    "-D",
                    str(data),
                    "-l",
                    str(LOCAL / "postgres.log"),
                    "-o",
                    f"-h 127.0.0.1 -p 55432 -k {LOCAL}",
                    "start",
                ],
                check=True,
            )
        import psycopg

        with psycopg.connect(
            "postgresql://annotation@127.0.0.1:55432/postgres", autocommit=True
        ) as connection:
            if not connection.execute(
                "SELECT 1 FROM pg_database WHERE datname='annotation_db'"
            ).fetchone():
                connection.execute("CREATE DATABASE annotation_db")
        redis = shutil.which("redis-server") or "/opt/homebrew/bin/redis-server"
        for port in (56379, 56380):
            import socket

            with socket.socket() as probe:
                present = probe.connect_ex(("127.0.0.1", port)) == 0
            if present:
                continue
            directory = LOCAL / f"redis-{port}"
            directory.mkdir(exist_ok=True)
            subprocess.run(
                [
                    redis,
                    "--bind",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--daemonize",
                    "yes",
                    "--dir",
                    str(directory),
                    "--pidfile",
                    str(directory / "redis.pid"),
                    "--logfile",
                    str(directory / "redis.log"),
                    "--appendonly",
                    "yes",
                    "--maxmemory-policy",
                    "noeviction" if port == 56379 else "allkeys-lru",
                    "--maxmemory",
                    "256mb",
                ],
                check=True,
            )
        print("本地 PostgreSQL 与两个 Redis 实例已准备。仅监听 127.0.0.1。")
        mongo = (
            shutil.which("mongod")
            or "/opt/homebrew/opt/mongodb-community@8.0/bin/mongod"
        )
        if Path(mongo).exists():
            mongo_dir = LOCAL / "mongo"
            mongo_dir.mkdir(exist_ok=True)
            with socket.socket() as probe:
                present = probe.connect_ex(("127.0.0.1", 57017)) == 0
            if not present:
                subprocess.run(
                    [
                        mongo,
                        "--dbpath",
                        str(mongo_dir),
                        "--bind_ip",
                        "127.0.0.1",
                        "--port",
                        "57017",
                        "--fork",
                        "--logpath",
                        str(LOCAL / "mongo.log"),
                    ],
                    check=True,
                )
            print("本地 MongoDB 已准备。")
    else:
        if data.exists():
            subprocess.run(
                [str(PG / "pg_ctl"), "-D", str(data), "stop", "-m", "fast"], check=False
            )
        for port in (56379, 56380):
            subprocess.run(
                ["/opt/homebrew/bin/redis-cli", "-p", str(port), "shutdown"],
                check=False,
            )
        import pymongo

        try:
            with pymongo.MongoClient(
                "mongodb://127.0.0.1:57017", serverSelectionTimeoutMS=500
            ) as client:
                client.admin.command("shutdown")
        except pymongo.errors.PyMongoError:
            pass


if __name__ == "__main__":
    main()
