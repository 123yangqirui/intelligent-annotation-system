# Vue3 + TypeScript 标注前端

```bash
cd /Users/bytedance/learn/futureproject/frontend
npm ci
npm run dev
```

入口：http://127.0.0.1:5173。后端和数据库必须同时运行。

## 页面

- `/login`：登录。
- `/`：项目列表和创建。
- `/projects/:id`：数据、规范、任务、导出、成员、训练评估和活动。
- `/annotate/:id`：文本分类或图像框编辑、历史、提交与审核。
- `/admin`：用户、API、模型、部署、事件。
- `/health`：保留阶段 1 连接检查。

## 学习入口

`src/main.ts` 创建应用，`router.ts` 配置页面路由与登录检查，`App.vue` 提供导航，`api/http.ts` 管理请求和会话。具体页面位于 `views/`，图像画布位于 `components/ImageEditor.vue`。

前端提示权限不代替 API 授权。密码和 access token 不写 localStorage。图片通过带身份的 API 获取，Object URL 在离开页面时释放。

修改后运行 `npm run build`，包含 Vue/TS 类型检查。

开发请求由 Vite `/api` 代理给 8000 端口，文档也通过 `/docs` 代理。正式静态部署使用 Nginx 配置。

完整启动、示例账号和模型条件见 [实现与运行说明](../docs/实现与运行说明.md)。
