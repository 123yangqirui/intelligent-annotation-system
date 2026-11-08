"""Repeatable module checks. Optional PostgreSQL run uses an isolated test database."""

import argparse
import ast
import json
import os
from pathlib import Path
import subprocess
import time
import yaml

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description="检查后端、前端、客户端和部署配置")
    parser.add_argument(
        "--postgres",
        action="store_true",
        help="额外运行本机 annotation_test PostgreSQL 集成测试",
    )
    args = parser.parse_args()
    destination = ROOT / ".local" / "verification"
    destination.mkdir(parents=True, exist_ok=True)
    backend = ROOT / "backend"
    frontend = ROOT / "frontend"
    client = ROOT / "client"
    steps = [
        (
            "backend_lint",
            backend,
            ["uv", "run", "ruff", "check", "app", "--select", "F401"],
            {},
        ),
        (
            "backend_tests",
            backend,
            [
                "uv",
                "run",
                "pytest",
                "-q",
                "--tb=short",
                "--cov=app",
                "--cov-report=json:" + str(destination / "coverage.json"),
                "--junitxml=" + str(destination / "backend.xml"),
            ],
            {"TEST_DATABASE_URL": ""},
        ),
    ]
    if args.postgres:
        steps.append(
            (
                "postgres_tests",
                backend,
                [
                    "uv",
                    "run",
                    "pytest",
                    "-q",
                    "--tb=short",
                    "--junitxml=" + str(destination / "postgres.xml"),
                ],
                {
                    "TEST_DATABASE_URL": "postgresql+psycopg://annotation@127.0.0.1:55432/annotation_test"
                },
            )
        )
    steps.extend(
        [
            ("frontend_tests", frontend, ["npm", "test"], {}),
            ("frontend_build", frontend, ["npm", "run", "build"], {}),
            ("frontend_audit", frontend, ["npm", "audit"], {}),
            ("client_tests", client, ["npm", "test"], {}),
            ("client_syntax", client, ["npm", "run", "check"], {}),
            ("client_audit", client, ["npm", "audit"], {}),
        ]
    )
    results = []
    for name, directory, command, environment in steps:
        print("检查：" + name, flush=True)
        start = time.monotonic()
        try:
            result = subprocess.run(
                command,
                cwd=directory,
                env={**os.environ, **environment},
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=300,
            )
            output = result.stdout
            code = result.returncode
        except (OSError, subprocess.TimeoutExpired) as error:
            output = str(error)
            code = 1
        (destination / (name + ".log")).write_text(output)
        results.append(
            {
                "module": name,
                "passed": code == 0,
                "seconds": round(time.monotonic() - start, 2),
                "log": str(destination / (name + ".log")),
            }
        )
        print(("通过：" if code == 0 else "失败：") + name, flush=True)
    for folder in ("backend/app", "scripts", "model_services"):
        for source in (ROOT / folder).rglob("*.py"):
            ast.parse(source.read_text(), filename=str(source))
    compose = yaml.safe_load((ROOT / "deploy" / "compose.yaml").read_text())
    assert {
        "api",
        "worker",
        "agent-worker",
        "dispatcher",
        "postgres",
        "mongo",
        "redis-broker",
        "redis-cache",
        "web",
    } <= set(compose["services"])
    assert not any(
        "docker.sock" in str(volume)
        for volume in compose["services"]["api"].get("volumes", [])
    )
    results.append({"module": "python_and_compose_static", "passed": True})
    (destination / "summary.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2)
    )
    print("报告：" + str(destination / "summary.json"))
    raise SystemExit(0 if all(x["passed"] for x in results) else 1)


if __name__ == "__main__":
    main()
