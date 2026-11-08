<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from "vue";
import {
  useRoute,
  RouterLink,
  useRouter,
  onBeforeRouteLeave,
  onBeforeRouteUpdate,
} from "vue-router";
import { api, post, fileBlob, user } from "../api/http";
import { workLabels, statusTone } from "../domain/status";
import ImageEditor, { type Box } from "../components/ImageEditor.vue";
const route = useRoute(),
  router = useRouter();
const data = ref<any>(null),
  project = ref<any>(null);
const labelId = ref(""),
  boxes = ref<Box[]>([]),
  explicitEmpty = ref(false),
  imageUrl = ref("");
const error = ref(""),
  notice = ref(""),
  busy = ref(false),
  dirty = ref(false),
  loading = ref(false),
  claimConflict = ref(false),
  reviewComment = ref("");
const history = ref<EditState[]>([]),
  future = ref<EditState[]>([]);
type EditState = { labelId: string; boxes: Box[]; explicitEmpty: boolean };
let previousUrl = "",
  baseId: string | null = null,
  loadGeneration = 0,
  claimTimer: number | undefined;
let claimed: { itemId: string; schemaId: string } | null = null;
let pendingSave: { fingerprint: string; operationId: string } | null = null;
const canAnnotate = computed(() =>
  ["admin", "owner", "annotator"].includes(project.value?.role),
);
const reviewMode = computed(
  () => project.value?.role === "reviewer" || route.query.review === "1",
);
const canReview = computed(
  () =>
    ["admin", "owner", "reviewer"].includes(project.value?.role) &&
    data.value?.work?.state === "submitted",
);
const canEdit = computed(
  () =>
    project.value?.status === "active" &&
    !claimConflict.value &&
    !loading.value &&
    (reviewMode.value ? canReview.value : canAnnotate.value),
);
function snapshot(): EditState {
  return {
    labelId: labelId.value,
    boxes: boxes.value.map((x) => ({ ...x, xyxy: [...x.xyxy] })),
    explicitEmpty: explicitEmpty.value,
  };
}
function applyEdit(state: EditState) {
  labelId.value = state.labelId;
  boxes.value = state.boxes;
  explicitEmpty.value = state.explicitEmpty;
  dirty.value = true;
}
async function release() {
  const old = claimed;
  claimed = null;
  if (old)
    await api(`/items/${old.itemId}/claim?schema_id=${old.schemaId}`, {
      method: "DELETE",
    }).catch(() => {});
}
async function renew() {
  if (!claimed) return;
  const current = claimed;
  try {
    await post(`/items/${current.itemId}/claim?schema_id=${current.schemaId}`);
  } catch (e) {
    if (claimed === current) {
      claimConflict.value = true;
      error.value = (e as Error).message;
    }
  }
}
async function load() {
  const generation = ++loadGeneration,
    itemId = String(route.params.id);
  loading.value = true;
  data.value = null;
  project.value = null;
  error.value = "";
  claimConflict.value = false;
  pendingSave = null;
  reviewComment.value = "";
  try {
    const item: any = await api(`/items/${itemId}`),
      info: any = await api(`/projects/${item.project_id}`);
    if (generation !== loadGeneration) return;
    if (!item.schema) throw new Error("请先在项目中发布标签规范");
    let nextUrl = "";
    if (item.object_key) {
      nextUrl = URL.createObjectURL(await fileBlob(`/items/${itemId}/file`));
      if (generation !== loadGeneration) {
        URL.revokeObjectURL(nextUrl);
        return;
      }
    }
    if (previousUrl) URL.revokeObjectURL(previousUrl);
    previousUrl = nextUrl;
    imageUrl.value = nextUrl;
    data.value = item;
    project.value = info;
    const revision = item.revisions.find(
        (x: any) => x.id === item.work.draft_id,
      ),
      content = revision?.content;
    baseId = revision?.id || null;
    labelId.value =
      content?.label_id ||
      (info.task_type === "image_detection" ? item.schema.labels[0]?.id : "") ||
      "";
    boxes.value = (content?.boxes || []).map((x: Box) => ({
      ...x,
      xyxy: [...x.xyxy],
    }));
    explicitEmpty.value = content?.explicit_empty || false;
    dirty.value = false;
    history.value = [];
    future.value = [];
    if (
      canAnnotate.value &&
      !reviewMode.value &&
      item.work.state !== "submitted" &&
      info.status === "active"
    ) {
      try {
        await post(`/items/${itemId}/claim?schema_id=${item.schema.id}`);
        if (generation === loadGeneration)
          claimed = { itemId, schemaId: item.schema.id };
      } catch (e) {
        claimConflict.value = true;
        error.value = (e as Error).message;
      }
    }
  } catch (e) {
    if (generation === loadGeneration) error.value = (e as Error).message;
  } finally {
    if (generation === loadGeneration) loading.value = false;
  }
}
function updateBoxes(value: Box[]) {
  if (!canEdit.value || busy.value) return;
  history.value.push(snapshot());
  future.value = [];
  boxes.value = value;
  explicitEmpty.value = false;
  dirty.value = true;
}
function undo() {
  if (!canEdit.value || busy.value) return;
  const old = history.value.pop();
  if (old) {
    future.value.push(snapshot());
    applyEdit(old);
  }
}
function redo() {
  if (!canEdit.value || busy.value) return;
  const next = future.value.pop();
  if (next) {
    history.value.push(snapshot());
    applyEdit(next);
  }
}
function usePrediction(revision: any) {
  if (!canEdit.value || busy.value) return;
  history.value.push(snapshot());
  future.value = [];
  baseId = revision.id;
  applyEdit({
    labelId: revision.content.label_id || labelId.value,
    boxes: (revision.content.boxes || []).map((x: Box) => ({
      ...x,
      xyxy: [...x.xyxy],
    })),
    explicitEmpty: revision.content.explicit_empty || false,
  });
  notice.value = "已采用为本地草稿，请检查并保存。";
}
async function action(fn: () => Promise<void>) {
  if (busy.value || loading.value) return;
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    await fn();
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
function content() {
  return project.value.task_type === "text_classification"
    ? { task_type: project.value.task_type, label_id: labelId.value }
    : {
        task_type: project.value.task_type,
        coordinate_space: "original_image_pixels",
        explicit_empty: explicitEmpty.value,
        boxes: snapshot().boxes,
      };
}
async function save() {
  if (!canEdit.value || reviewMode.value)
    throw new Error("当前不能保存标注；审核修正请使用“修正并接受”。");
  if (!labelId.value && project.value.task_type === "text_classification")
    throw new Error("请先选择一个类别。");
  const payload = {
      schema_id: data.value.schema.id,
      content: content(),
      base_id: baseId,
      expected_version: data.value.work.version,
    },
    fingerprint = JSON.stringify(payload);
  if (!pendingSave || pendingSave.fingerprint !== fingerprint)
    pendingSave = { fingerprint, operationId: crypto.randomUUID() };
  const result: any = await post(`/items/${data.value.id}/revisions`, {
    ...payload,
    operation_id: pendingSave.operationId,
  });
  pendingSave = null;
  data.value.work = result.work;
  baseId = result.revision.id;
  if (!data.value.revisions.some((x: any) => x.id === result.revision.id))
    data.value.revisions.unshift(result.revision);
  dirty.value = false;
  notice.value = "草稿已保存。";
}
async function submit() {
  if (dirty.value || !data.value.work.draft_id) await save();
  data.value.work = await post(`/items/${data.value.id}/submit`, {
    schema_id: data.value.schema.id,
    revision_id: data.value.work.draft_id,
    expected_version: data.value.work.version,
  });
  claimed = null;
  notice.value = "已提交审核。";
}
async function review(decision: string) {
  if (dirty.value)
    throw new Error("有尚未提交的审核修正，请使用“修正并接受”或放弃本地修改。");
  await post(`/revisions/${data.value.work.submitted_id}/reviews`, {
    decision,
    expected_version: data.value.work.version,
    operation_id: crypto.randomUUID(),
    comment: reviewComment.value,
  });
  await load();
  notice.value = decision === "accept" ? "审核通过。" : "已退回修改。";
}
async function correctAndReview() {
  await post(
    `/revisions/${data.value.work.submitted_id}/correction?comment=${encodeURIComponent(reviewComment.value)}`,
    {
      schema_id: data.value.schema.id,
      expected_version: data.value.work.version,
      operation_id: crypto.randomUUID(),
      content: content(),
    },
  );
  await load();
  notice.value = "已保存审核修正并接受。";
}
async function leave() {
  if (dirty.value) {
    if (reviewMode.value) {
      error.value = "审核修正尚未提交，请完成修正或放弃本地修改后离开。";
      return false;
    }
    try {
      await save();
    } catch (e) {
      error.value = (e as Error).message;
      return false;
    }
  }
  await release();
  return true;
}
async function next() {
  if (!(await leave())) return;
  if (reviewMode.value) {
    const queue: any[] = await api(`/projects/${project.value.id}/reviews`);
    const nextItem = queue.find((x) => x.item_id !== data.value.id);
    if (nextItem) await router.push(`/annotate/${nextItem.item_id}?review=1`);
    else notice.value = "当前没有其他待审核数据。";
  } else {
    const result: any = await post(
      `/projects/${project.value.id}/claims/next?exclude_item_id=${data.value.id}`,
    );
    if (result.item_id) await router.push(`/annotate/${result.item_id}`);
    else notice.value = "没有其他可领取的数据。";
  }
}
onBeforeRouteLeave(leave);
onBeforeRouteUpdate(leave);
watch(() => route.params.id, load, { immediate: true });
function beforeUnload(event: BeforeUnloadEvent) {
  if (dirty.value) {
    event.preventDefault();
    event.returnValue = "";
  }
}
function shortcut(event: KeyboardEvent) {
  const input =
    event.target instanceof HTMLElement &&
    (event.target.isContentEditable ||
      ["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName));
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
    event.preventDefault();
    if (canEdit.value && !reviewMode.value) action(save);
    return;
  }
  if (input || busy.value || loading.value) return;
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    event.preventDefault();
    if (canAnnotate.value && !reviewMode.value) action(submit);
  } else if (
    (event.ctrlKey || event.metaKey) &&
    event.key.toLowerCase() === "z"
  ) {
    event.preventDefault();
    event.shiftKey ? redo() : undo();
  } else if (/^[1-9]$/.test(event.key) && canEdit.value) {
    const label = data.value?.schema?.labels[Number(event.key) - 1];
    if (label) {
      labelId.value = label.id;
      dirty.value = true;
    }
  } else if (event.altKey && event.key === "ArrowRight") {
    event.preventDefault();
    action(next);
  }
}
onMounted(() => {
  window.addEventListener("beforeunload", beforeUnload);
  window.addEventListener("keydown", shortcut);
  claimTimer = window.setInterval(renew, 60000);
});
onUnmounted(() => {
  loadGeneration++;
  window.clearInterval(claimTimer);
  release();
  if (previousUrl) URL.revokeObjectURL(previousUrl);
  window.removeEventListener("beforeunload", beforeUnload);
  window.removeEventListener("keydown", shortcut);
});
</script>
<template>
  <main class="annotation-workspace" v-if="data">
    <div class="page-title annotation-toolbar">
      <div>
        <RouterLink :to="`/projects/${data.project_id}`" class="back-link"
          >← {{ project?.name || "返回项目" }}</RouterLink
        >
        <h1>标注工作台</h1>
        <p class="muted small">
          {{ data.external_id || data.id }} · 规范版本
          {{ data.schema?.version }} · 工作版本 {{ data.work?.version }}
          <span class="badge" :class="statusTone(data.work?.state)">{{
            workLabels[data.work?.state]
          }}</span>
        </p>
      </div>
      <div class="inline-controls">
        <button
          class="btn"
          :disabled="busy || !canEdit || reviewMode"
          @click="action(save)"
        >
          保存草稿{{ dirty ? " *" : "" }}</button
        ><button
          class="btn primary"
          :disabled="busy || !canEdit || reviewMode"
          @click="action(submit)"
        >
          提交审核</button
        ><button class="btn" :disabled="busy" @click="action(next)">
          下一条 →
        </button>
      </div>
    </div>
    <p class="error" v-if="error" role="alert">{{ error }}</p>
    <p class="notice" v-if="notice">{{ notice }}</p>
    <div class="work-hints">
      <span>{{
        loading ? "正在加载…" : dirty ? "有未保存修改" : "当前修改已保存"
      }}</span
      ><span
        >1–9 选择标签 · Ctrl/⌘ S 保存 · Ctrl/⌘ Enter 提交 · Alt → 下一条</span
      ><button
        v-if="dirty || claimConflict"
        class="quiet"
        @click="
          dirty = false;
          action(load);
        "
      >
        放弃本地修改并重新加载
      </button>
    </div>
    <div v-if="data.schema && !loading" class="annotation-grid">
      <section class="panel data-surface">
        <template v-if="project?.task_type === 'text_classification'"
          ><p class="kicker">待标注文本</p>
          <div class="text-content">{{ data.text }}</div></template
        ><ImageEditor
          v-else-if="imageUrl"
          :src="imageUrl"
          :model-value="boxes"
          :labels="data.schema.labels"
          :label-id="labelId"
          :disabled="busy || !canEdit"
          @update:model-value="updateBoxes"
        />
        <div
          v-if="project?.task_type === 'image_detection'"
          class="inline-controls"
        >
          <button
            class="btn"
            :disabled="busy || !canEdit || !history.length"
            @click="undo"
          >
            撤销</button
          ><button
            class="btn"
            :disabled="busy || !canEdit || !future.length"
            @click="redo"
          >
            重做</button
          ><label class="checkbox"
            ><input
              type="checkbox"
              v-model="explicitEmpty"
              @change="dirty = true"
              :disabled="busy || !canEdit || boxes.length > 0"
            />已检查，确实无目标</label
          ><span class="muted small"
            >{{ data.width }} × {{ data.height }} ·
            {{ boxes.length }} 个框</span
          >
        </div>
      </section>
      <aside class="annotation-sidebar">
        <section class="panel stack">
          <h2>选择类别</h2>
          <button
            v-for="label in data.schema.labels"
            :key="label.id"
            class="label-choice"
            :class="{ selected: labelId === label.id }"
            :disabled="busy || !canEdit"
            @click="
              labelId = label.id;
              dirty = true;
            "
          >
            <span class="label-dot" :style="{ background: label.color }"></span
            >{{ label.name }}
          </button>
          <p class="muted small">{{ data.schema.rules.instructions }}</p>
        </section>
        <section
          class="panel stack"
          v-if="
            data.work.state === 'submitted' &&
            ['admin', 'owner', 'reviewer'].includes(project?.role)
          "
        >
          <h2>审核当前提交</h2>
          <textarea
            v-model="reviewComment"
            placeholder="审核意见 / 退回原因"
          ></textarea>
          <div class="inline-controls">
            <button class="btn primary" @click="action(() => review('accept'))">
              接受</button
            ><button class="btn" @click="action(() => review('return'))">
              退回</button
            ><button class="btn" @click="action(correctAndReview)">
              修正并接受
            </button>
          </div>
        </section>
        <section v-if="data.reviews?.length" class="panel stack">
          <h2>审核反馈</h2>
          <article
            v-for="feedback in data.reviews"
            :key="feedback.id"
            class="revision-card"
          >
            <strong>{{
              feedback.decision === "accept" ? "审核通过" : "退回修改"
            }}</strong>
            <p>{{ feedback.comment || "未填写意见" }}</p>
            <span class="muted small"
              >{{ feedback.reviewer_name }} ·
              {{ new Date(feedback.created_at).toLocaleString("zh-CN") }}</span
            >
          </article>
        </section>
        <section class="panel stack">
          <h2>修订历史与模型建议</h2>
          <article
            class="revision-card"
            v-for="revision in data.revisions"
            :key="revision.id"
          >
            <div class="row-between">
              <span class="badge">{{
                revision.source === "human"
                  ? "人工标注"
                  : revision.source === "model"
                    ? "模型建议"
                    : "导入标签"
              }}</span
              ><span class="small muted">{{ revision.id.slice(0, 8) }}</span>
            </div>
            <p class="small">
              {{
                revision.content.label_id
                  ? data.schema.labels.find(
                      (x: any) => x.id === revision.content.label_id,
                    )?.name
                  : `${revision.content.boxes?.length || 0} 个框`
              }}
            </p>
            <button
              class="quiet"
              :disabled="busy || !canEdit"
              @click="usePrediction(revision)"
            >
              采用为草稿
            </button>
          </article>
        </section>
      </aside>
    </div>
  </main>
  <main v-else class="workspace">
    <p class="error">{{ error || "加载数据…" }}</p>
  </main>
</template>
