import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import AgentPanel from "../src/components/AgentPanel.vue";
import AgentSettings from "../src/components/AgentSettings.vue";
const mock = vi.hoisted(() => ({ api: vi.fn(), post: vi.fn(), counter: 0 }));
vi.mock("../src/api/http", () => ({
  api: mock.api,
  post: mock.post,
  jobHeaders: () => ({ "Idempotency-Key": "stable-key-" + ++mock.counter }),
}));
const props = {
  projectId: "project",
  taskType: "text_classification",
  datasets: [{ id: "dataset", name: "数据版本", status: "ready" }],
  models: [{ id: "model", name: "分类模型", task_type: "text_classification" }],
  datasetId: "dataset",
  modelId: "model",
};
const run = () => ({
  id: "run",
  instruction: "预标注两条",
  status: "queued",
  questions: [],
  summary: {},
  error: null,
  plan: null,
  tool_calls: [],
});
let wrappers: ReturnType<typeof mount>[] = [];
beforeEach(() => {
  vi.clearAllMocks();
  mock.counter = 0;
  mock.api.mockImplementation(async (path: string) =>
    path.endsWith("/agent-configs")
      ? [{ id: "config", name: "助手", engine: "http_plan" }]
      : path.endsWith("/agent-runs")
        ? []
        : run(),
  );
  mock.post.mockResolvedValue(run());
});
afterEach(() => {
  for (const wrapper of wrappers) wrapper.unmount();
  wrappers = [];
});
async function open() {
  const wrapper = mount(AgentPanel, { props });
  wrappers.push(wrapper);
  await flushPromises();
  return wrapper;
}
it("Shows setup instructions without inventing an available Agent", async () => {
  mock.api.mockResolvedValue([]);
  const w = await open();
  expect(w.text()).toContain("当前没有已授权");
  expect(w.find("button.primary").attributes("disabled")).toBeDefined();
});
it("Binds project selection and sample limit without enabling automatic review", async () => {
  const w = await open();
  await w.get("textarea").setValue("预标注两条");
  await w.get("input[type=number]").setValue(2);
  await w.find("form").trigger("submit");
  await flushPromises();
  const [path, body] = mock.post.mock.calls[0]!;
  expect(path).toBe("/projects/project/agent-runs");
  expect(body.max_items).toBe(2);
  expect(body.dataset_id).toBe("dataset");
  expect(body.allowed_actions).toEqual(["prediction", "summary"]);
});
it("Reuses the creation key after a failed response or failed detail fetch", async () => {
  const w = await open();
  await w.get("textarea").setValue("预标注两条");
  mock.post.mockRejectedValueOnce(new Error("网络失败"));
  await w.find("form").trigger("submit");
  await flushPromises();
  await w.find("form").trigger("submit");
  await flushPromises();
  expect(mock.post.mock.calls[0]![2]["Idempotency-Key"]).toBe(
    mock.post.mock.calls[1]![2]["Idempotency-Key"],
  );
});
it("Displays real summaries and lets a waiting run receive clarification", async () => {
  const waiting = {
    ...run(),
    status: "needs_input",
    questions: ["选择哪个模型？"],
  };
  mock.api.mockImplementation(async (path: string) =>
    path.endsWith("/agent-configs")
      ? [{ id: "config", name: "助手" }]
      : path.endsWith("/agent-runs")
        ? [waiting]
        : waiting,
  );
  const w = await open();
  await w.get(".agent-run-link").trigger("click");
  await flushPromises();
  expect(w.text()).toContain("选择哪个模型");
  const fields = w.findAll("textarea");
  await fields[1]!.setValue("使用分类模型");
  await w.findAll("form")[1]!.trigger("submit");
  await flushPromises();
  expect(mock.post.mock.calls[0]![0]).toBe("/agent-runs/run/input");
  expect(mock.post.mock.calls[0]![1]).toEqual({ message: "使用分类模型" });
});
it("Registers an explicitly configured engine and edits only configuration fields", async () => {
  mock.api.mockImplementation(async (path: string) =>
    path === "/admin/agent-configs"
      ? [
          {
            id: "config",
            name: "助手",
            engine: "http_plan",
            protocol: "openai",
            provider_id: "provider",
            system_prompt: "规则",
            skill_ids: [],
            enabled: true,
            max_steps: 8,
            max_turns: 6,
            max_output_tokens: 2048,
            timeout_seconds: 90,
            created_at: "ignored",
          },
        ]
      : [],
  );
  const w = mount(AgentSettings, {
    props: {
      providers: [{ id: "provider", name: "服务", model_name: "planner" }],
    },
  });
  wrappers.push(w);
  await flushPromises();
  const edit = w.findAll("button").find((x) => x.text() === "编辑")!;
  await edit.trigger("click");
  await w.find("form").trigger("submit");
  await flushPromises();
  const call = mock.api.mock.calls.find(
    ([path, options]) =>
      path === "/admin/agent-configs/config" && options?.method === "PUT",
  )!;
  expect(JSON.parse(call[1].body).engine).toBe("http_plan");
  expect(JSON.parse(call[1].body)).not.toHaveProperty("created_at");
});
