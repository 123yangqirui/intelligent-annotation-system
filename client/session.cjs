const { httpUrl } = require("./collector.cjs");

function createSession(fetchService = fetch) {
  let server = "",
    token = "",
    cookie = "",
    generation = 0,
    refreshing = null;
  function clear() {
    token = "";
    cookie = "";
    refreshing = null;
    generation++;
  }
  async function send(route, options = {}, authenticated = true) {
    const headers = new Headers(options.headers);
    if (authenticated && token) headers.set("Authorization", "Bearer " + token);
    if (route.startsWith("/auth/") && cookie) headers.set("Cookie", cookie);
    const response = await fetchService(server + "/api/v1" + route, {
      ...options,
      headers,
      redirect: "error",
      signal: AbortSignal.timeout(30000),
    });
    return response;
  }
  function saveCookie(response) {
    const values = response.headers.getSetCookie?.() || [
      response.headers.get("set-cookie") || "",
    ];
    for (const value of values) {
      if (value.startsWith("refresh_token=")) cookie = value.split(";", 1)[0];
    }
  }
  async function data(response) {
    const value = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(
        value.error?.message || "服务请求失败：" + response.status,
      );
      error.status = response.status;
      throw error;
    }
    return value;
  }
  async function refresh() {
    if (refreshing) return refreshing;
    const current = generation;
    const operation = (async () => {
      const response = await send("/auth/refresh", { method: "POST" }, false);
      const value = await data(response);
      if (generation !== current) throw new Error("登录状态已改变，请重试");
      saveCookie(response);
      token = value.access_token;
    })();
    refreshing = operation;
    try {
      await operation;
    } catch (error) {
      if (generation === current) clear();
      throw error;
    } finally {
      if (refreshing === operation) refreshing = null;
    }
  }
  async function request(route, options = {}) {
    if (!token) throw new Error("请先登录");
    const current = generation,
      sentToken = token;
    let response = await send(route, options);
    if (generation !== current) throw new Error("登录状态已改变，请重试");
    if (response.status === 401 && cookie) {
      // A delayed 401 may arrive after another request already refreshed.
      if (token === sentToken) await refresh();
      if (generation !== current) throw new Error("登录状态已改变，请重试");
      response = await send(route, options);
    }
    return data(response);
  }
  async function login(address, username, password) {
    clear();
    server = httpUrl(address);
    const current = generation;
    const response = await send(
      "/auth/login",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      },
      false,
    );
    const value = await data(response);
    if (generation !== current) throw new Error("登录状态已改变，请重试");
    token = value.access_token;
    saveCookie(response);
    return { user: value.user, projects: await request("/projects") };
  }
  async function logout() {
    try {
      if (cookie) await data(await send("/auth/logout", { method: "POST" }));
    } finally {
      clear();
    }
  }
  return { request, login, logout };
}
module.exports = { createSession };
