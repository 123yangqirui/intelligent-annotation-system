# FastAPI 标注后端

后端当前包含身份、项目、数据、规范、修订、审核、导出、模型、任务、部署、评估和 Agent 接口。

```bash
cd /Users/bytedance/learn/futureproject/backend
uv sync --locked
uv run python ../scripts/local_infra.py start
uv run alembic upgrade head
uv run app/main.py
```

API 文档：http://127.0.0.1:8000/docs。数据库与 Worker 启动方式、初始化管理员、模型条件见 [实现与运行说明](../docs/实现与运行说明.md)。

`GET /api/v1/health` 保留阶段 1 响应；业务接口需要登录。`GET /api/v1/health/db` 在登录后检查数据库。

主要目录：`app/` 业务与任务，`alembic/` 明确的数据库迁移，`tests/` 隔离集成测试。backend/.env 保存签名与加密主密钥，不提交。

本机运行默认 PostgreSQL，SQLite 只用于临时测试。执行 `uv run pytest -q` 验证。

## Agent

运行 `uv run python -m app.cli seed-agent-skills` 初始化内置技能，启动监听 `agent` 队列的 Worker。配置与验证见 [Agent 实现与使用说明](../docs/Agent实现与使用说明.md)。
