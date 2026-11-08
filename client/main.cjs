const { app, BrowserWindow, ipcMain } = require("electron");
const path = require("node:path");
const { collect } = require("./collector.cjs");
const { createSession } = require("./session.cjs");
const session = createSession();
let window;
ipcMain.handle("collector", async (event, action, payload) => {
  if (event.sender !== window.webContents) throw new Error("不允许的调用来源");
  if (action === "login") {
    return session.login(payload.server, payload.username, payload.password);
  }
  if (action === "logout") return session.logout();
  if (action === "job")
    return session.request("/jobs/" + encodeURIComponent(payload.id));
  if (action === "collect") {
    const { data, name, source } = await collect(payload.url, payload.kind);
    const form = new FormData();
    form.append("file", new Blob([data]), name);
    form.append("source_url", source);
    return session.request(
      "/projects/" + encodeURIComponent(payload.project) + "/imports",
      {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
        body: form,
      },
    );
  }
  throw new Error("未知操作");
});
app.whenReady().then(() => {
  window = new BrowserWindow({
    width: 900,
    height: 760,
    title: "智能标注 · 资料采集客户端",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
    },
  });
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event) => event.preventDefault());
  window.loadFile(path.join(__dirname, "index.html"));
});
app.on("window-all-closed", () => app.quit());
