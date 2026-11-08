# 资料采集桌面客户端

基于 Electron 的独立客户端，支持登录标注服务、选择项目、从指定 HTTP/HTTPS 地址提取网页文本或下载图片，并通过正式导入 API 提交任务和显示进度。需要项目负责人或管理员权限。

```bash
cd /Users/bytedance/learn/futureproject/client
npm ci
npm start
```

如果 Electron 运行时下载失败，执行 `ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/ npm run install-runtime` 后再启动。脚本会使用 npm 包附带的官方 SHA-256 校验文件检查运行时，校验失败时不会安装。

Web 后端与 Worker 必须运行。服务地址默认 `http://127.0.0.1:8000`；另一台电脑上填写服务器实际地址。

登录 token 与刷新 Cookie 只保存在主进程内存，渲染页面不能读取；令牌过期自动刷新，上传重试保留幂等键；退出会撤销刷新会话，关闭客户端后需要重新登录。不抓取需要额外登录或执行 JavaScript 后才显示内容的页面，也不绕过来源网站的访问限制。只采集你有权使用的资料。

源码启动已提供；当前不包含签名、公证和自动更新安装包。`npm run check` 检查主进程、preload 和页面脚本语法。另有命令行版本 `scripts/collector.py`。

2026-11-05 在 macOS ARM 上已实际启动 Electron 45.2.3，验证登录、项目选择、本地静态网页文本采集和后台导入成功。图片下载路径与动态网页兼容性需要按实际资料来源继续验证。

2026-11-06 重新启动进程，11 项测试通过。同一业务模块的真实 HTTP 验收验证过期令牌刷新、文本与图片导入、退出；本轮窗口自动化无法取得可操作窗口，未宣称完成桌面按钮级验收。详见 [全模块运行验收报告](../docs/全模块运行验收与问题修复报告.md)。
