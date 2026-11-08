import { beforeEach, expect, it, vi } from "vitest";
const fetchMock = vi.fn();
beforeEach(() => {
  vi.resetModules();
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
});
const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
it("Refreshes a shared session only once for concurrent requests", async () => {
  const { restoreSession, user } = await import("../src/api/http");
  fetchMock.mockResolvedValue(
    json({ access_token: "token", user: { id: "1", username: "demo" } }),
  );
  expect(await Promise.all([restoreSession(), restoreSession()])).toEqual([
    true,
    true,
  ]);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(user.value?.username).toBe("demo");
});
it("Retries the original write with its idempotency key after refreshing", async () => {
  const { api } = await import("../src/api/http");
  fetchMock
    .mockResolvedValueOnce(json({}, 401))
    .mockResolvedValueOnce(json({ access_token: "fresh", user: { id: "1" } }))
    .mockResolvedValueOnce(json({ ok: true }));
  expect(
    await api("/items/1/revisions", {
      method: "POST",
      body: '{"operation_id":"stable"}',
      headers: { "Idempotency-Key": "stable" },
    }),
  ).toEqual({ ok: true });
  const last = fetchMock.mock.calls[2]![1];
  expect(last.body).toBe('{"operation_id":"stable"}');
  expect(last.headers.get("Authorization")).toBe("Bearer fresh");
  expect(last.headers.get("Idempotency-Key")).toBe("stable");
});
it("Keeps upload boundaries and exposes useful API errors", async () => {
  const { api, ApiError } = await import("../src/api/http");
  fetchMock.mockResolvedValueOnce(json({ ok: true }));
  await api("/imports", { method: "POST", body: new FormData() });
  expect(fetchMock.mock.calls[0]![1].headers.has("Content-Type")).toBe(false);
  fetchMock.mockResolvedValueOnce(
    json(
      {
        error: {
          code: "REVISION_CONFLICT",
          message: "版本冲突",
          request_id: "trace",
        },
      },
      409,
    ),
  );
  await expect(api("/items")).rejects.toMatchObject({
    status: 409,
    code: "REVISION_CONFLICT",
    requestId: "trace",
    message: "版本冲突",
  });
  fetchMock.mockRejectedValueOnce(new TypeError("offline"));
  await expect(api("/items")).rejects.toBeInstanceOf(ApiError);
});
it("Clears memory authentication when refresh is rejected", async () => {
  const { restoreSession, user } = await import("../src/api/http");
  user.value = { id: "1", username: "demo", is_admin: false, enabled: true };
  fetchMock.mockResolvedValue(json({}, 401));
  expect(await restoreSession()).toBe(false);
  expect(user.value).toBeNull();
});
