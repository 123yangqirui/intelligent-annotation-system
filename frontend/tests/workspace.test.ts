import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { reactive } from "vue";
import AnnotationView from "../src/views/AnnotationView.vue";
import ProjectView from "../src/views/ProjectView.vue";
const mock = vi.hoisted(() => ({
  api: vi.fn(),
  post: vi.fn(),
  route: {} as any,
  leave: [] as Function[],
  push: vi.fn(),
}));
vi.mock("../src/api/http", () => ({
  api: mock.api,
  post: mock.post,
  fileBlob: vi.fn(),
  user: { value: { id: "owner", is_admin: false } },
  jobHeaders: () => ({ "Idempotency-Key": "stable" }),
  download: vi.fn(),
}));
vi.mock("vue-router", () => ({
  useRoute: () => mock.route,
  useRouter: () => ({ push: mock.push }),
  RouterLink: { props: ["to"], template: "<a><slot /></a>" },
  onBeforeRouteLeave: (f: Function) => mock.leave.push(f),
  onBeforeRouteUpdate: () => {},
}));
function item(id = "1") {
  return {
    id,
    project_id: "p",
    text: "文本 " + id,
    schema: {
      id: "s",
      version: 1,
      labels: [
        { id: "A", name: "类别 A", color: "#4268e8" },
        { id: "B", name: "类别 B", color: "#4268e8" },
      ],
      rules: {},
    },
    work: { version: 1, state: "unstarted", draft_id: null },
    revisions: [],
    reviews: [],
  };
}
let wrappers: ReturnType<typeof mount>[] = [];
beforeEach(() => {
  vi.clearAllMocks();
  mock.leave = [];
  mock.route = reactive({ params: { id: "1" }, query: {} });
  mock.api.mockImplementation(async (path: string) =>
    path.startsWith("/items/")
      ? item(String(mock.route.params.id))
      : path === "/projects/p"
        ? {
            id: "p",
            name: "项目",
            role: "owner",
            status: "active",
            task_type: "text_classification",
          }
        : [],
  );
  mock.post.mockImplementation(async (path: string, body: any) =>
    path.endsWith("/revisions")
      ? {
          revision: { id: "new", source: "human", content: body.content },
          work: { version: 2, state: "draft", draft_id: "new" },
        }
      : {},
  );
});
afterEach(() => {
  for (const wrapper of wrappers) wrapper.unmount();
  wrappers = [];
});
async function open(component: any = AnnotationView) {
  const wrapper = mount(component, {
    global: { stubs: { ImageEditor: true } },
  });
  wrappers.push(wrapper);
  await flushPromises();
  return wrapper;
}
function button(wrapper: any, text: string) {
  return wrapper.findAll("button").find((x: any) => x.text().includes(text))!;
}
it("Requires explicit classification and supports keyboard save", async () => {
  const w = await open();
  await button(w, "提交审核").trigger("click");
  expect(w.text()).toContain("请先选择一个类别");
  await button(w, "类别 B").trigger("click");
  window.dispatchEvent(
    new KeyboardEvent("keydown", { key: "s", ctrlKey: true }),
  );
  await flushPromises();
  const call = mock.post.mock.calls.find(([path]) =>
    path.endsWith("/revisions"),
  )!;
  expect(call[1].content.label_id).toBe("B");
  expect(w.text()).toContain("草稿已保存");
});
it("Failed writes preserve content and reuse the operation id", async () => {
  const w = await open();
  await button(w, "类别 A").trigger("click");
  const original = mock.post.getMockImplementation()!;
  let failed = false;
  mock.post.mockImplementation(async (path, body) => {
    if (path.endsWith("/revisions") && !failed) {
      failed = true;
      throw new Error("网络失败");
    }
    return original(path, body);
  });
  await button(w, "保存草稿").trigger("click");
  await flushPromises();
  expect(w.text()).toContain("网络失败");
  await button(w, "保存草稿").trigger("click");
  await flushPromises();
  const writes = mock.post.mock.calls.filter(([path]) =>
    path.endsWith("/revisions"),
  );
  expect(writes[0]![1].operation_id).toBe(writes[1]![1].operation_id);
  expect(writes[1]![1].content.label_id).toBe("A");
});
it("Blocks leaving when a modified draft cannot be saved", async () => {
  const w = await open();
  await button(w, "类别 A").trigger("click");
  mock.post.mockRejectedValue(new Error("冲突"));
  expect(await mock.leave[0]!()).toBe(false);
  expect(w.text()).toContain("冲突");
});
it("Reloads the reused component when the item route changes", async () => {
  const w = await open();
  expect(w.text()).toContain("文本 1");
  mock.route.params.id = "2";
  await flushPromises();
  expect(w.text()).toContain("文本 2");
  expect(w.text()).not.toContain("文本 1");
});

it("Does not leave the previous sample editable when the next load fails", async () => {
  const w = await open();
  mock.api.mockRejectedValue(new Error("数据不存在"));
  mock.route.params.id = "missing";
  await flushPromises();
  expect(w.text()).toContain("数据不存在");
  expect(w.text()).not.toContain("文本 1");
  expect(w.findAll(".label-choice")).toHaveLength(0);
});

it("Ignores an older request that finishes after the new route loads", async () => {
  let resolveOld: (value: any) => void = () => {};
  mock.api.mockImplementation(async (path: string) =>
    path === "/items/1"
      ? new Promise((resolve) => {
          resolveOld = resolve;
        })
      : path === "/items/2"
        ? item("2")
        : path === "/projects/p"
          ? {
              id: "p",
              name: "项目",
              role: "owner",
              status: "active",
              task_type: "text_classification",
            }
          : [],
  );
  const w = await open();
  mock.route.params.id = "2";
  await flushPromises();
  resolveOld(item("1"));
  await flushPromises();
  expect(w.text()).toContain("文本 2");
  expect(w.text()).not.toContain("文本 1");
});

it("Makes completed review pages read only even for project owners", async () => {
  mock.route.query.review = "1";
  mock.api.mockImplementation(async (path: string) =>
    path.startsWith("/items/")
      ? { ...item(), work: { version: 5, state: "accepted" } }
      : path === "/projects/p"
        ? {
            id: "p",
            role: "owner",
            name: "项目",
            status: "active",
            task_type: "text_classification",
          }
        : [],
  );
  const w = await open();
  expect(button(w, "类别 A").attributes("disabled")).toBeDefined();
});
it("Shows review feedback and keeps corrections separate from ordinary save", async () => {
  mock.route.query.review = "1";
  mock.api.mockImplementation(async (path: string) =>
    path.startsWith("/items/")
      ? {
          ...item(),
          work: { version: 3, state: "submitted", submitted_id: "original" },
          reviews: [
            {
              id: "feedback",
              decision: "return",
              comment: "请检查规则",
              reviewer_name: "老师",
            },
          ],
        }
      : path === "/projects/p"
        ? {
            id: "p",
            name: "项目",
            role: "reviewer",
            status: "active",
            task_type: "text_classification",
          }
        : [],
  );
  const w = await open();
  expect(w.text()).toContain("请检查规则");
  expect(button(w, "保存草稿").attributes("disabled")).toBeDefined();
  await button(w, "类别 B").trigger("click");
  await button(w, "接受").trigger("click");
  await flushPromises();
  expect(w.text()).toContain("有尚未提交的审核修正");
  expect(await mock.leave[0]!()).toBe(false);
  await button(w, "修正并接受").trigger("click");
  await flushPromises();
  const correction = mock.post.mock.calls.find(([path]) =>
    path.includes("/correction"),
  )!;
  expect(correction[1].content.label_id).toBe("B");
});
it("Hides management controls from annotators", async () => {
  mock.api.mockImplementation(async (path: string) =>
    path === "/projects/1"
      ? {
          id: "1",
          name: "项目",
          role: "annotator",
          status: "active",
          task_type: "text_classification",
          work_counts: { unstarted: 0 },
          item_count: 0,
          accepted_count: 0,
        }
      : [],
  );
  const w = await open(ProjectView);
  expect(w.text()).toContain("数据列表");
  expect(w.text()).not.toContain("提交导入任务");
  expect(w.text()).not.toContain("训练与评估");
  expect(w.text()).not.toContain("项目成员");
});
