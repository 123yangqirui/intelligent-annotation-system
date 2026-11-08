const $ = (id) => document.getElementById(id);
let timer;
function disconnect() {
  clearInterval(timer);
  $("project").replaceChildren();
  $("collect-button").disabled = true;
  $("logout").disabled = true;
  $("account").textContent = "尚未连接";
}
function status(message, error = false) {
  $("status").textContent = message;
  $("status").className = error ? "error" : "notice";
}
const states = {
  queued: "排队中",
  running: "处理中",
  cancel_requested: "正在取消",
  succeeded: "导入完成",
  partially_failed: "部分导入失败",
  failed: "导入失败",
  cancelled: "已取消",
};
function showJob(job) {
  $("result").textContent = [
    "任务编号：" + job.id,
    "状态：" + (states[job.status] || job.status),
    job.result
      ? "已导入 " +
        (job.result.imported || 0) +
        " 条；错误 " +
        (job.result.errors || 0) +
        " 条"
      : "后台正在处理，请稍候。",
    ...(job.errors || []).map(
      (x) => (x.source ? x.source + "：" : "") + x.message,
    ),
  ].join("\n");
}
$("login").addEventListener("submit", async (event) => {
  event.preventDefault();
  disconnect();
  try {
    const data = await window.collector.call("login", {
      server: $("server").value,
      username: $("username").value,
      password: $("password").value,
    });
    $("password").value = "";
    $("account").textContent = "已连接：" + data.user.username;
    $("logout").disabled = false;
    $("project").replaceChildren();
    for (const project of data.projects) {
      const option = document.createElement("option");
      option.value = project.id;
      option.textContent = project.name + " · " + project.task_type;
      $("project").appendChild(option);
    }
    $("collect-button").disabled = !data.projects.length;
    status("连接成功，请选择有导入权限的项目。");
  } catch (error) {
    status(error.message, true);
  }
});
$("logout").addEventListener("click", async () => {
  try {
    await window.collector.call("logout", {});
    status("已退出登录。");
  } catch (error) {
    status(error.message, true);
  } finally {
    disconnect();
  }
});
$("collect").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("collect-button").disabled = true;
  status("正在采集并提交导入…");
  clearInterval(timer);
  try {
    const job = await window.collector.call("collect", {
      project: $("project").value,
      url: $("url").value,
      kind: $("kind").value,
    });
    showJob(job);
    status("资料已提交，正在等待后台导入。");
    timer = setInterval(async () => {
      try {
        const result = await window.collector.call("job", { id: job.id });
        showJob(result);
        if (
          ["succeeded", "partially_failed", "failed", "cancelled"].includes(
            result.status,
          )
        ) {
          clearInterval(timer);
          status(states[result.status], result.status !== "succeeded");
        }
      } catch (error) {
        clearInterval(timer);
        status(error.message, true);
      }
    }, 3000);
  } catch (error) {
    status(error.message, true);
  } finally {
    $("collect-button").disabled = false;
  }
});
