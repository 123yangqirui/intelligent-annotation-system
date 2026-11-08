# 智能标注系统

Vue3 + TypeScript + FastAPI 的文本与图像标注工程。已加入身份权限、项目和标签规范、数据版本、人工标注、审核导出、模型插件与 API 配置、后台任务、PostgreSQL/MongoDB/Redis，以及资料采集客户端。已增加自然语言 Agent、Claude Agent SDK 接入、技能管理和可恢复工作流。

## 打开系统

[Web 工作空间](http://127.0.0.1:5173/) · [API 文档](http://127.0.0.1:8000/docs)

本机管理员用户名：`admin`。随机密码保存在 `.local/admin-password`；该目录已忽略，不提交到版本控制。

## 启动

终端 1：

```bash
cd /Users/bytedance/learn/futureproject/backend
uv sync --locked
uv run python ../scripts/local_infra.py start
uv run alembic upgrade head
uv run app/main.py
```

终端 2：

```bash
cd /Users/bytedance/learn/futureproject/backend
uv run celery -A app.tasks.celery_app worker -Q annotation,training,deployment --pool=threads --concurrency=2 --loglevel=info
```

终端 3：

```bash
cd /Users/bytedance/learn/futureproject/backend
uv run python -m app.cli dispatch
```

终端 4：

```bash
cd /Users/bytedance/learn/futureproject/frontend
npm ci
npm run dev
```

终端 5，Agent Worker：

```bash
cd /Users/bytedance/learn/futureproject/backend
uv run celery -A app.tasks.celery_app worker -Q agent --pool=threads --concurrency=2 --hostname=agent@%h --loglevel=info
```

Agent 配置、技能与测试边界见 [Agent 实现与使用说明](./docs/Agent实现与使用说明.md)。真实规划需要配置供应商凭证。

数据库与队列仅需启动一份。已有服务正在运行时不用重复启动。初始化、Docker、训练、备份和客户端见 [实现与运行说明](./docs/实现与运行说明.md)。

## 源码导航

- `backend/app/models.py`：业务表和关联。
- `backend/app/api.py`：接口、授权和事务用例。
- `backend/app/annotations.py`：标签、坐标和版本校验。
- `backend/app/tasks.py`：队列、执行、租约和恢复。
- `backend/app/inference.py`：模型调用、预算与缓存。
- `backend/app/agent/`：规划引擎、技能、业务工具与可恢复执行。
- `frontend/src/views/`：登录、项目、标注和管理员页面。
- `model_services/`：BERT/YOLO 服务与训练脚本。
- `client/`：Electron 资料采集客户端。
- `deploy/`：Docker Compose、Nginx 和镜像。

## 验证

```bash
cd /Users/bytedance/learn/futureproject/backend
uv run python ../scripts/check.py
```

本机 PostgreSQL 已运行时，可以追加 `--postgres`；只使用独立 `annotation_test` 数据库。检查日志保存在 `.local/verification/`。本轮后端 106 项在 SQLite 和 PostgreSQL 中均通过，前端 20 项、客户端 11 项测试通过。真实服务、浏览器、备份恢复与修复情况见 [全模块运行验收报告](./docs/全模块运行验收与问题修复报告.md)。基础系统不依赖大模型凭证；自动预标注需要真实模型配置。

## 设计文档

- [整个项目的零基础代码导读](./docs/整个项目的零基础代码导读.md)
- [前端布局优化说明](./docs/前端布局优化说明.md)
- [实现与运行说明](./docs/实现与运行说明.md)
- [Agent 实现与使用说明](./docs/Agent实现与使用说明.md)
- [全模块运行验收与问题修复报告](./docs/全模块运行验收与问题修复报告.md)
- [优化与模块测试报告](./docs/优化与模块测试报告.md)
- [详细设计与实现方案](./docs/智能标注系统详细设计与实现方案.md)
- [零基础开发路线](./docs/零基础逐模块开发路线.md)
- [概要工程设计](./docs/智能标注系统工程设计.md)
