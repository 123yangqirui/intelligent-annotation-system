// Runs the desktop client's actual session/collector modules against live services.
const fs = require("node:fs/promises");
const path = require("node:path");
const assert = require("node:assert/strict");
const { createSession } = require("../client/session.cjs");
const { collect } = require("../client/collector.cjs");
const root = path.resolve(__dirname, "..");

async function main() {
  const folder = path.join(root, ".local/runtime-acceptance");
  const fixture = JSON.parse(
    await fs.readFile(path.join(folder, "report.json"), "utf8"),
  );
  assert.equal(fixture.fixture, true);
  assert.equal(fixture.status, "passed");
  const expired = (
    await fs.readFile(path.join(folder, "expired-access.txt"), "utf8")
  ).trim();
  const password = (
    await fs.readFile(path.join(root, ".local/admin-password"), "utf8")
  ).trim();
  let fault = true,
    refreshes = 0,
    actual401 = false;
  const session = createSession(async (url, options) => {
    if (url.endsWith("/auth/refresh")) refreshes++;
    if (url.endsWith("/projects") && fault) {
      fault = false;
      const headers = new Headers(options.headers);
      headers.set("Authorization", "Bearer " + expired);
      const response = await fetch(url, { ...options, headers });
      assert.equal(response.status, 401);
      actual401 = true;
      return response;
    }
    return fetch(url, options);
  });
  try {
    const login = await session.login(
      "http://127.0.0.1:8000",
      "admin",
      password,
    );
    assert.equal(login.user.username, "admin");
    assert.equal(refreshes, 1);
    const jobs = [];
    for (const [kind, task, url] of [
      ["text", "text_classification", fixture.source_url],
      ["image", "image_detection", fixture.image_url],
    ]) {
      assert.ok(url.startsWith("http://127.0.0.1:"));
      const payload = await collect(url, kind);
      if (kind === "text") {
        const row = JSON.parse(payload.data.toString());
        assert.ok(row.text.includes("Public fixture article"));
        assert.ok(!row.text.includes("SCRIPT") && !row.text.includes("NAV"));
        assert.equal(row.source_url, url);
      } else assert.equal(payload.data.subarray(1, 4).toString(), "PNG");
      const form = new FormData();
      form.append("file", new Blob([payload.data]), payload.name);
      form.append("source_url", payload.source);
      const job = await session.request(
        "/projects/" + fixture.projects[task].id + "/imports",
        {
          method: "POST",
          headers: { "Idempotency-Key": crypto.randomUUID() },
          body: form,
        },
      );
      const deadline = Date.now() + 60000;
      let result;
      do {
        result = await session.request("/jobs/" + job.id);
        if (
          ["succeeded", "partially_failed", "failed", "cancelled"].includes(
            result.status,
          )
        )
          break;
        await new Promise((resolve) => setTimeout(resolve, 200));
      } while (Date.now() < deadline);
      assert.equal(result.status, "succeeded");
      assert.equal(result.result.imported, 1);
      jobs.push({
        kind,
        job_id: job.id,
        status: result.status,
        imported: result.result.imported,
      });
    }
    await session.logout();
    await assert.rejects(session.request("/projects"), /请先登录/);
    const report = {
      status: "passed",
      actual_expired_token_401: actual401,
      refreshes,
      jobs,
      logout_cleared: true,
      scope:
        "Actual desktop session/collector modules and live HTTP/Celery; native window automation unavailable",
    };
    await fs.writeFile(
      path.join(folder, "client-live.json"),
      JSON.stringify(report, null, 2),
    );
    console.log(JSON.stringify(report));
  } finally {
    await session.logout().catch(() => {});
    await fs.unlink(path.join(folder, "expired-access.txt")).catch(() => {});
  }
}
main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
