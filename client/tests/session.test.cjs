const { test } = require("node:test");
const assert = require("node:assert/strict");
const { createSession } = require("../session.cjs");
const json = (data, status = 200, cookie) =>
  new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json",
      ...(cookie ? { "Set-Cookie": cookie } : {}),
    },
  });
const loggedIn = () =>
  json(
    { access_token: "old", user: { username: "fixture" } },
    200,
    "refresh_token=first; HttpOnly; Path=/api/v1/auth",
  );

test("Refreshes an expired session and preserves upload body and idempotency key", async () => {
  const calls = [];
  const session = createSession(async (url, options) => {
    calls.push({ url, options });
    if (url.endsWith("/auth/login")) return loggedIn();
    if (url.endsWith("/projects")) return json([]);
    if (url.endsWith("/auth/refresh")) {
      assert.equal(options.headers.get("Cookie"), "refresh_token=first");
      return json(
        { access_token: "fresh" },
        200,
        "refresh_token=second; HttpOnly",
      );
    }
    assert.equal(options.headers.get("Cookie"), null);
    return options.headers.get("Authorization") === "Bearer old"
      ? json({}, 401)
      : json({ id: "job" });
  });
  await session.login("http://localhost:8000", "fixture", "test-password");
  const body = new FormData();
  body.append("file", new Blob(["fixture"]), "data.jsonl");
  assert.deepEqual(
    await session.request("/imports", {
      method: "POST",
      headers: { "Idempotency-Key": "stable" },
      body,
    }),
    { id: "job" },
  );
  const uploads = calls.filter((x) => x.url.endsWith("/imports"));
  assert.equal(uploads.length, 2);
  assert.equal(uploads[1].options.body, body);
  assert.equal(uploads[1].options.headers.get("Idempotency-Key"), "stable");
  assert.equal(uploads[1].options.headers.get("Content-Type"), null);
});

test("A delayed concurrent 401 reuses the refreshed token without rotating twice", async () => {
  let refreshes = 0,
    finished;
  const refreshed = new Promise((resolve) => {
    finished = resolve;
  });
  const session = createSession(async (url, { headers }) => {
    if (url.endsWith("/auth/login")) return loggedIn();
    if (url.endsWith("/projects")) return json([]);
    if (url.endsWith("/auth/refresh")) {
      refreshes++;
      finished();
      return json({ access_token: "fresh" }, 200, "refresh_token=second");
    }
    if (headers.get("Authorization") === "Bearer old") {
      if (url.endsWith("/b")) await refreshed;
      return json({}, 401);
    }
    return json({ ok: true });
  });
  await session.login("http://localhost:8000", "fixture", "test-password");
  assert.deepEqual(
    await Promise.all([session.request("/a"), session.request("/b")]),
    [{ ok: true }, { ok: true }],
  );
  assert.equal(refreshes, 1);
});

test("A rejected refresh clears authentication and blocks further protected requests", async () => {
  const session = createSession(async (url) =>
    url.endsWith("/auth/login")
      ? loggedIn()
      : url.endsWith("/projects")
        ? json([])
        : json({ error: { message: "expired" } }, 401),
  );
  await session.login("http://localhost:8000", "fixture", "test-password");
  await assert.rejects(session.request("/jobs/one"), /expired/);
  await assert.rejects(session.request("/jobs/two"), /请先登录/);
});

test("Switching servers never sends credentials from the previous server", async () => {
  const session = createSession(async (url, { headers }) => {
    if (url.startsWith("http://second")) {
      assert.equal(headers.get("Cookie"), null);
      assert.equal(headers.get("Authorization"), null);
      return json({ error: { message: "invalid login" } }, 401);
    }
    return url.endsWith("/auth/login") ? loggedIn() : json([]);
  });
  await session.login("http://first:8000", "fixture", "test-password");
  await assert.rejects(
    session.login("http://second:8000", "fixture", "wrong"),
    /invalid login/,
  );
  await assert.rejects(session.request("/projects"), /请先登录/);
});

test("Logout revokes the refresh session and clears in-memory authentication", async () => {
  const session = createSession(async (url, { headers }) => {
    if (url.endsWith("/auth/login")) return loggedIn();
    if (url.endsWith("/auth/logout"))
      assert.equal(headers.get("Cookie"), "refresh_token=first");
    return json([]);
  });
  await session.login("http://localhost:8000", "fixture", "test-password");
  await session.logout();
  await assert.rejects(session.request("/projects"), /请先登录/);
});
