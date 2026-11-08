<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from "vue";
import { workLabels, jobLabels, jobTypes, statusTone } from "../domain/status";
import { useRoute, RouterLink } from "vue-router";
import { api, post, jobHeaders, download, user } from "../api/http";
import AgentPanel from "../components/AgentPanel.vue";
import AppIcon from "../components/AppIcon.vue";
const route = useRoute();
const id = String(route.params.id);
const project = ref<any>(null);
const schemas = ref<any[]>([]);
const datasets = ref<any[]>([]);
const items = ref<any[]>([]);
const jobs = ref<any[]>([]);
const models = ref<any[]>([]);
const exports = ref<any[]>([]);
const audit = ref<any[]>([]);
const members = ref<any[]>([]);
const tab = ref("data");
const error = ref("");
const notice = ref("");
const busy = ref(false);
const labelText = ref("");
const rules = ref("");
const datasetId = ref("");
const modelId = ref("");
const importOpen = ref(false);
const selectedDataset = computed(() =>
  datasets.value.find((dataset) => dataset.id === datasetId.value),
);
const datasetLabels: Record<string, string> = {
  staging: "待发布",
  needs_resolution: "待处理",
  ready: "已发布",
};
const nextItem = computed(() =>
  items.value.find((item) =>
    ["unstarted", "draft", "returned"].includes(
      item.work?.state || "unstarted",
    ),
  ),
);
const canAnnotate = computed(
  () =>
    ["admin", "owner", "annotator"].includes(project.value?.role) &&
    project.value?.status === "active",
);
const format = ref("jsonl");
const purpose = ref("none");
const split = ref("train");
const file = ref<File | null>(null);
const mapping = ref("{}");
const text = ref("");
const datasetName = ref("");
const memberId = ref("");
const memberRole = ref("annotator");
const allUsers = ref<any[]>([]);
const trainConfig = ref('{"checkpoint":"","epochs":3,"batch_size":8}');
let timer: number | undefined;
const tabs = [
  ["reviews", "待审核"],
  ["data", "数据"],
  ["schema", "标签规范"],
  ["jobs", "后台任务"],
  ["exports", "导出"],
  ["members", "成员"],
  ["training", "训练与评估"],
  ["audit", "活动记录"],
  ["agent", "Agent 助手"],
];
const offset = ref(0);
const filterState = ref("");
const reviewQueue = ref<any[]>([]);
const canManage = computed(
  () =>
    ["admin", "owner"].includes(project.value?.role) &&
    project.value?.status === "active",
);
const canReview = computed(() =>
  ["admin", "owner", "reviewer"].includes(project.value?.role),
);
const visibleTabs = computed(() =>
  tabs.filter(
    ([key]) =>
      ["data", "jobs"].includes(key!) ||
      (["schema", "exports", "members", "training", "audit", "agent"].includes(
        key!,
      ) &&
        ["admin", "owner"].includes(project.value?.role)) ||
      (key === "reviews" && canReview.value),
  ),
);
async function action(fn: () => Promise<void>) {
  if (busy.value) return;
  error.value = "";
  notice.value = "";
  busy.value = true;
  try {
    await fn();
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
async function load() {
  project.value = await api(`/projects/${id}`);
  [schemas.value, datasets.value, models.value] = await Promise.all([
    api(`/projects/${id}/schemas`),
    api(`/projects/${id}/datasets`),
    api(`/projects/${id}/models`),
  ]);
  if (!models.value.some((model) => model.id === modelId.value))
    modelId.value = "";
  format.value =
    project.value.task_type === "text_classification" ? "jsonl" : "yolo";
  if (!datasetId.value) datasetId.value = datasets.value[0]?.id || "";
  await refreshItems();
  jobs.value = await api(`/projects/${id}/jobs`);
  if (project.value.role === "owner" || user.value?.is_admin) {
    exports.value = await api(`/projects/${id}/exports`);
    members.value = await api(`/projects/${id}/members`);
    audit.value = await api(`/projects/${id}/audit`);
  }
  if (canReview.value) reviewQueue.value = await api(`/projects/${id}/reviews`);
  if (user.value?.is_admin) allUsers.value = await api("/admin/users");
}
async function refreshItems() {
  const params = new URLSearchParams({
    offset: String(offset.value),
    limit: "50",
  });
  if (datasetId.value) params.set("dataset_id", datasetId.value);
  if (filterState.value) params.set("state", filterState.value);
  items.value = await api(`/projects/${id}/items?${params}`);
}
async function createSchema() {
  await action(async () => {
    const labels = labelText.value
      .split("\n")
      .map((x) => x.trim())
      .filter(Boolean)
      .map((name) => ({ name }));
    await post(`/projects/${id}/schemas`, {
      labels,
      rules: { instructions: rules.value },
    });
    await load();
    notice.value = "规范草稿已创建，请检查后发布。";
  });
}
async function publishSchema(schemaId: string) {
  await action(async () => {
    await post(`/schemas/${schemaId}/publish`);
    await load();
  });
}
async function newDataset() {
  await action(async () => {
    const data: any = await post(
      `/projects/${id}/datasets?name=${encodeURIComponent(datasetName.value || "手工数据集")}`,
    );
    datasetId.value = data.id;
    await load();
  });
}
async function addText() {
  await action(async () => {
    await post(`/projects/${id}/texts`, {
      dataset_id: datasetId.value,
      text: text.value,
    });
    text.value = "";
    await load();
  });
}
async function importData() {
  await action(async () => {
    if (!file.value) throw new Error("请选择文件");
    const data = new FormData();
    data.append("file", file.value);
    data.append("labels_purpose", purpose.value);
    data.append("label_mapping", mapping.value);
    data.append("split", split.value);
    if (project.value.active_schema_id)
      data.append("schema_id", project.value.active_schema_id);
    await api(`/projects/${id}/imports`, {
      method: "POST",
      body: data,
      headers: jobHeaders(),
    });
    tab.value = "jobs";
    notice.value = "导入任务已提交。";
    await load();
  });
}
async function publishData() {
  await action(async () => {
    await post(`/datasets/${datasetId.value}/publish`);
    await load();
  });
}
async function forkData() {
  await action(async () => {
    const fork: any = await post(`/datasets/${datasetId.value}/fork`);
    datasetId.value = fork.id;
    await load();
  });
}
async function splitData() {
  await action(async () => {
    const counts: any = await post(`/datasets/${datasetId.value}/split`);
    notice.value = `划分完成：train ${counts.train} / val ${counts.val} / test ${counts.test}`;
  });
}
async function predict() {
  await action(async () => {
    await post(
      `/projects/${id}/prediction-jobs`,
      {
        dataset_id: datasetId.value,
        schema_id: project.value.active_schema_id,
        model_id: modelId.value,
      },
      jobHeaders(),
    );
    tab.value = "jobs";
    await load();
  });
}
async function exportData() {
  await action(async () => {
    await post(
      `/projects/${id}/exports`,
      {
        dataset_id: datasetId.value,
        schema_id: project.value.active_schema_id,
        format: format.value,
      },
      jobHeaders(),
    );
    await load();
    notice.value = "已固定审核快照，正在后台打包。";
  });
}
async function addMember() {
  await action(async () => {
    await post(
      `/projects/${id}/members?user_id=${memberId.value}&role=${memberRole.value}`,
    );
    await load();
  });
}
async function train() {
  await action(async () => {
    await post(
      `/projects/${id}/training-runs`,
      {
        dataset_id: datasetId.value,
        schema_id: project.value.active_schema_id,
        recipe:
          project.value.task_type === "text_classification" ? "bert" : "yolo",
        config: JSON.parse(trainConfig.value),
      },
      jobHeaders(),
    );
    tab.value = "jobs";
    await load();
  });
}
async function evaluate() {
  await action(async () => {
    await post(
      `/projects/${id}/evaluations`,
      {
        dataset_id: datasetId.value,
        schema_id: project.value.active_schema_id,
        model_id: modelId.value,
      },
      jobHeaders(),
    );
    tab.value = "jobs";
    await load();
  });
}
onMounted(async () => {
  await action(load);
  timer = window.setInterval(async () => {
    try {
      jobs.value = await api(`/projects/${id}/jobs`);
    } catch {}
  }, 3000);
});
onUnmounted(() => window.clearInterval(timer));
</script>
<template>
  <main class="workspace" v-if="project">
    <RouterLink class="back-link" to="/">← 所有项目</RouterLink>
    <div class="page-title">
      <div>
        <p class="kicker">
          {{
            project.task_type === "text_classification"
              ? "文本分类"
              : "图像目标检测"
          }}
        </p>
        <h1>{{ project.name }}</h1>
        <p class="muted">
          {{ project.description || "组织数据，完成标注与审核。" }}
        </p>
      </div>
      <span
        class="badge"
        :class="project.status === 'active' ? 'success' : ''"
        >{{ project.status === "active" ? "进行中" : "已归档" }}</span
      >
    </div>
    <div class="project-stats">
      <div>
        <span>数据总量</span><strong>{{ project.item_count }}</strong
        ><small>当前项目</small>
      </div>
      <div>
        <span>待标注</span
        ><strong>{{
          (project.work_counts?.unstarted || 0) +
          (project.work_counts?.returned || 0)
        }}</strong
        ><small
          >草稿 {{ project.work_counts?.draft || 0 }} · 已退回
          {{ project.work_counts?.returned || 0 }}</small
        >
      </div>
      <div>
        <span>待审核</span
        ><strong>{{ project.work_counts?.submitted || 0 }}</strong
        ><small>已提交的标注</small>
      </div>
      <div class="completion-stat">
        <span>审核完成率</span
        ><strong
          >{{
            project.item_count
              ? Math.round((project.accepted_count / project.item_count) * 100)
              : 0
          }}<small>%</small></strong
        >
        <div class="completion-track">
          <span
            :style="{
              width: `${project.item_count ? (project.accepted_count / project.item_count) * 100 : 0}%`,
            }"
          ></span>
        </div>
        <small>已审核 {{ project.accepted_count }} 条</small>
      </div>
    </div>
    <div class="tabs" role="tablist" aria-label="项目模块">
      <button
        v-for="[key, title] in visibleTabs"
        :key="key"
        :class="{ active: tab === key }"
        role="tab"
        :aria-selected="tab === key"
        @click="
          tab = key!;
          action(load);
        "
      >
        {{ title }}
      </button>
    </div>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="notice" class="notice">{{ notice }}</p>
    <AgentPanel
      v-if="tab === 'agent'"
      :project-id="id"
      :task-type="project.task_type"
      :datasets="datasets"
      :models="models"
      :dataset-id="datasetId"
      :model-id="modelId"
    />
    <section v-if="tab === 'schema'" class="two-columns">
      <form class="panel" @submit.prevent="createSchema">
        <h2>创建规范版本</h2>
        <label
          >标签（每行一个）<textarea
            v-model="labelText"
            rows="6"
            required
            placeholder="退款&#10;物流&#10;商品质量&#10;其他"
          ></textarea></label
        ><label
          >标注规则<textarea
            v-model="rules"
            placeholder="类别含义、冲突处理与示例"
          ></textarea></label
        ><button class="btn primary" :disabled="busy">保存草稿</button>
      </form>
      <div>
        <article class="panel stack" v-for="schema in schemas" :key="schema.id">
          <div class="row-between">
            <h2>版本 {{ schema.version }}</h2>
            <span class="badge">{{
              schema.status === "published" ? "已发布" : "草稿"
            }}</span>
          </div>
          <div class="chips">
            <span v-for="label in schema.labels" :key="label.id">{{
              label.name
            }}</span>
          </div>
          <p class="muted small">{{ schema.rules.instructions }}</p>
          <button
            v-if="schema.status === 'draft'"
            class="btn"
            @click="publishSchema(schema.id)"
          >
            发布并设为当前规范
          </button>
        </article>
        <p v-if="!schemas.length" class="muted">先创建标签规范，再开始标注。</p>
      </div>
    </section>
    <section v-if="tab === 'reviews'" class="panel table-panel">
      <h2>待审核工作队列</h2>
      <p class="muted small">
        审核具体提交版本；退回意见会显示在标注员的工作台。
      </p>
      <table>
        <thead>
          <tr>
            <th>数据</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="entry in reviewQueue" :key="entry.id">
            <td>
              {{
                entry.text?.slice(0, 90) || entry.external_id || entry.item_id
              }}
            </td>
            <td>
              <RouterLink :to="`/annotate/${entry.item_id}?review=1`"
                >进入审核 →</RouterLink
              >
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="!reviewQueue.length" class="empty muted">当前没有待审核提交。</p>
    </section>
    <section
      v-if="tab === 'data'"
      class="data-layout"
      :class="{ 'data-layout-simple': !canManage }"
    >
      <div class="data-main">
        <div class="panel dataset-toolbar">
          <label for="dataset-selection">数据版本</label>
          <select
            id="dataset-selection"
            v-model="datasetId"
            @change="
              offset = 0;
              action(refreshItems);
            "
          >
            <option value="">全部数据</option>
            <option
              v-for="dataset in datasets"
              :key="dataset.id"
              :value="dataset.id"
            >
              {{ dataset.name }} ·
              {{ datasetLabels[dataset.status] || dataset.status }}
            </option>
          </select>
          <span
            v-if="selectedDataset"
            class="badge"
            :class="selectedDataset.status === 'ready' ? 'success' : 'pending'"
            >{{
              datasetLabels[selectedDataset.status] || selectedDataset.status
            }}</span
          >
        </div>
        <div class="panel data-table-panel">
          <div class="section-heading">
            <div>
              <h2>
                数据列表 <span class="count-pill">{{ items.length }}</span>
              </h2>
              <p class="muted small">
                选择数据开始标注，提交后由审核人员确认。
              </p>
            </div>
            <div class="inline-controls">
              <button
                v-if="canManage"
                class="btn"
                :aria-expanded="importOpen"
                @click="importOpen = !importOpen"
              >
                <AppIcon name="plus" />导入数据</button
              ><RouterLink
                v-if="canAnnotate && nextItem"
                class="btn primary"
                :to="`/annotate/${nextItem.id}`"
                >开始标注 <AppIcon name="arrow"
              /></RouterLink>
            </div>
          </div>
          <div class="table-filter-bar">
            <span class="muted small">当前页 {{ items.length }} 条</span
            ><select
              v-model="filterState"
              aria-label="筛选标注状态"
              @change="
                offset = 0;
                action(refreshItems);
              "
            >
              <option value="">全部状态</option>
              <option
                v-for="(title, state) in workLabels"
                :key="state"
                :value="state"
              >
                {{ title }}
              </option>
            </select>
          </div>
          <div class="table-scroll">
            <table class="data-table">
              <thead>
                <tr>
                  <th>数据内容</th>
                  <th>标注状态</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in items" :key="item.id">
                  <td>
                    <div class="item-cell">
                      <span class="item-icon"
                        ><AppIcon
                          :name="
                            project.task_type === 'text_classification'
                              ? 'text'
                              : 'image'
                          " /></span
                      ><span>{{
                        item.text?.slice(0, 90) || item.external_id || item.id
                      }}</span>
                    </div>
                  </td>
                  <td>
                    <span class="badge" :class="statusTone(item.work?.state)">{{
                      workLabels[item.work?.state || "unstarted"] || "未开始"
                    }}</span>
                  </td>
                  <td>
                    <RouterLink
                      class="table-action"
                      :to="`/annotate/${item.id}`"
                      >标注 / 审核 <AppIcon name="arrow"
                    /></RouterLink>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-if="!items.length" class="empty">
            <AppIcon name="projects" />
            <h3>暂无符合条件的数据</h3>
            <p class="muted small">调整筛选条件，或先导入数据。</p>
          </div>
          <div class="row-between pagination">
            <span class="muted small"
              >第 {{ Math.floor(offset / 50) + 1 }} 页 · 每页 50 条</span
            >
            <div class="inline-controls">
              <button
                class="btn"
                :disabled="offset === 0"
                @click="
                  offset = Math.max(0, offset - 50);
                  action(refreshItems);
                "
              >
                上一页</button
              ><button
                class="btn"
                :disabled="items.length < 50"
                @click="
                  offset += 50;
                  action(refreshItems);
                "
              >
                下一页
              </button>
            </div>
          </div>
        </div>
      </div>
      <aside
        v-if="canManage"
        class="data-sidebar"
        aria-label="数据准备与预标注"
      >
        <div v-if="canManage" class="panel sidebar-panel">
          <h2>版本管理</h2>
          <p class="muted small">已发布版本用于标注与导出。</p>
          <div class="version-actions">
            <button
              class="btn"
              :disabled="!datasetId || busy"
              @click="forkData"
            >
              复制为新版本</button
            ><button
              class="btn"
              :disabled="selectedDataset?.status !== 'staging' || busy"
              @click="splitData"
            >
              按 7:2:1 划分数据</button
            ><button
              class="btn primary"
              :disabled="
                !['staging', 'needs_resolution'].includes(
                  selectedDataset?.status || '',
                ) || busy
              "
              @click="publishData"
            >
              发布当前数据版本
            </button>
          </div>
          <details class="settings-details">
            <summary>创建手工版本</summary>
            <label
              >新版本名称<input
                v-model="datasetName"
                placeholder="例如：新增资料" /></label
            ><button class="btn" :disabled="busy" @click="newDataset">
              创建手工版本
            </button>
          </details>
          <details
            v-if="project.task_type === 'text_classification'"
            class="settings-details"
          >
            <summary>手工录入文本</summary>
            <label
              >文本内容<textarea
                v-model="text"
                rows="3"
                placeholder="录入一条待标注文本"
              ></textarea>
            </label>
            <p v-if="selectedDataset?.status !== 'staging'" class="muted small">
              选择待发布版本后录入文本。
            </p>
            <button
              class="btn"
              :disabled="
                selectedDataset?.status !== 'staging' || !text.trim() || busy
              "
              @click="addText"
            >
              添加文本
            </button>
          </details>
        </div>
        <details
          v-if="canManage"
          class="panel sidebar-panel import-details"
          :open="importOpen"
          @toggle="
            importOpen = ($event.currentTarget as HTMLDetailsElement).open
          "
        >
          <summary>
            <span><AppIcon name="plus" />导入数据</span
            ><span class="details-chevron">⌄</span>
          </summary>
          <form @submit.prevent="importData">
            <p class="muted small">
              {{
                project.task_type === "text_classification"
                  ? "支持 CSV / JSONL 文本文件。"
                  : "支持单张图片、ZIP、YOLO / COCO 数据。"
              }}
            </p>
            <label
              >文件<input
                type="file"
                required
                @change="
                  file = ($event.target as HTMLInputElement).files?.[0] || null
                "
            /></label>
            <div class="form-grid">
              <label
                >数据划分<select v-model="split">
                  <option value="train">训练集 train</option>
                  <option value="val">验证集 val</option>
                  <option value="test">测试集 test</option>
                </select></label
              ><label
                >附带标签用途<select v-model="purpose">
                  <option value="none">不导入标签</option>
                  <option value="seed">预标注，等待审核</option>
                  <option value="evaluation">隔离的评估真值</option>
                </select></label
              >
            </div>
            <label v-if="purpose !== 'none'"
              >类别映射 JSON<input
                v-model="mapping"
                placeholder='{"0":"标签 UUID"}' /></label
            ><button class="btn primary" :disabled="busy">提交导入任务</button>
          </form>
        </details>
        <div v-if="canManage" class="panel sidebar-panel">
          <h2>自动预标注</h2>
          <p class="muted small">使用模型生成候选，再进行人工审核。</p>
          <label
            >标注模型<select v-model="modelId">
              <option value="">选择可用模型</option>
              <option
                v-for="model in models.filter(
                  (x) => x.task_type === project.task_type,
                )"
                :key="model.id"
                :value="model.id"
              >
                {{ model.name }}
              </option>
            </select></label
          ><button
            class="btn"
            :disabled="!modelId || selectedDataset?.status !== 'ready' || busy"
            @click="predict"
          >
            批量预测
          </button>
        </div>
      </aside>
    </section>
    <section v-if="tab === 'jobs'">
      <article class="panel stack" v-for="job in jobs" :key="job.id">
        <div class="row-between">
          <h2>
            {{ jobTypes[job.type] || job.type }}
            <span class="badge" :class="statusTone(job.status)">{{
              jobLabels[job.status] || job.status
            }}</span>
          </h2>
          <span class="muted small">{{ job.id.slice(0, 8) }}</span>
        </div>
        <p class="muted small">
          成功 {{ job.counts.succeeded }} · 失败 {{ job.counts.failed }} ·
          待处理 {{ job.counts.pending }} · 取消 {{ job.counts.cancelled }}
        </p>
        <pre v-if="job.errors?.length">{{
          JSON.stringify(job.errors, null, 2)
        }}</pre>
        <pre v-if="job.result">{{ JSON.stringify(job.result, null, 2) }}</pre>
        <div class="inline-controls">
          <button
            v-if="['queued', 'running'].includes(job.status)"
            class="btn"
            @click="
              action(async () => {
                await post(`/jobs/${job.id}/cancel`);
                await load();
              })
            "
          >
            取消</button
          ><button
            v-if="
              ['failed', 'partially_failed', 'cancelled'].includes(job.status)
            "
            class="btn"
            @click="
              action(async () => {
                await post(`/jobs/${job.id}/retry`, undefined, jobHeaders());
                await load();
              })
            "
          >
            重试失败项</button
          ><button
            class="btn"
            @click="
              action(async () => {
                const details: any = await api(`/jobs/${job.id}`);
                notice = JSON.stringify(
                  details.items.filter((x: any) => x.error),
                );
              })
            "
          >
            查看逐项错误
          </button>
        </div>
      </article>
      <p v-if="!jobs.length" class="panel empty muted">还没有后台任务。</p>
    </section>
    <section v-if="tab === 'exports'">
      <div class="panel stack">
        <h2>导出已审核数据</h2>
        <p class="muted">
          导出固定数据、规范与修订快照，只包含人工审核通过的结果。
        </p>
        <div class="inline-controls">
          <select v-model="format">
            <option v-if="project.task_type === 'text_classification'">
              jsonl
            </option>
            <option v-if="project.task_type === 'text_classification'">
              csv
            </option>
            <option v-if="project.task_type === 'image_detection'">yolo</option>
            <option v-if="project.task_type === 'image_detection'">
              coco
            </option></select
          ><button class="btn primary" @click="exportData">创建导出</button>
        </div>
      </div>
      <article
        v-for="item in exports"
        :key="item.id"
        class="panel stack row-between"
      >
        <span
          >{{ item.format }} ·
          {{ new Date(item.created_at).toLocaleString("zh-CN") }}</span
        ><button
          class="btn"
          :disabled="!item.object_key"
          @click="
            action(async () =>
              download(`/exports/${item.id}/download`, 'annotations.zip'),
            )
          "
        >
          {{ item.object_key ? "下载 ZIP" : "正在准备" }}
        </button>
      </article>
    </section>
    <section v-if="tab === 'members'" class="panel">
      <h2>项目成员</h2>
      <table>
        <thead>
          <tr>
            <th>用户</th>
            <th>角色</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="member in members" :key="member.id">
            <td>{{ member.username }}</td>
            <td>{{ member.role }}</td>
            <td>
              <button
                class="quiet"
                @click="
                  action(async () => {
                    await api(`/projects/${id}/members/${member.id}`, {
                      method: 'DELETE',
                    });
                    await load();
                  })
                "
              >
                移除
              </button>
            </td>
          </tr>
        </tbody>
      </table>
      <div class="inline-controls">
        <select v-if="allUsers.length" v-model="memberId">
          <option value="">选择用户</option>
          <option v-for="u in allUsers" :value="u.id" :key="u.id">
            {{ u.username }}
          </option></select
        ><input v-else v-model="memberId" placeholder="用户 ID" /><select
          v-model="memberRole"
        >
          <option>annotator</option>
          <option>reviewer</option>
          <option>owner</option></select
        ><button class="btn" @click="addMember">添加 / 修改角色</button>
      </div>
    </section>
    <section v-if="tab === 'training'" class="panel stack">
      <h2>训练与评估</h2>
      <p class="muted">
        训练使用当前数据版本的已审核快照，至少包含 train 和 val 两个划分。训练
        Worker 需要独立安装 ML 依赖。
      </p>
      <label
        >训练参数 JSON<textarea
          v-model="trainConfig"
          rows="6"
        ></textarea></label
      ><button class="btn primary" @click="train">提交训练</button>
      <hr />
      <p class="muted">
        评估仅由评估任务读取隔离真值，预测模型不会收到参考标签。
      </p>
      <select v-model="modelId">
        <option value="">选择模型</option>
        <option v-for="model in models" :key="model.id" :value="model.id">
          {{ model.name }}
        </option></select
      ><button class="btn" @click="evaluate">评估当前数据版本</button>
    </section>
    <section v-if="tab === 'audit'" class="panel">
      <h2>活动记录</h2>
      <table>
        <thead>
          <tr>
            <th>时间</th>
            <th>动作</th>
            <th>详情</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="entry in audit" :key="entry.id">
            <td>{{ new Date(entry.created_at).toLocaleString("zh-CN") }}</td>
            <td>{{ entry.action }}</td>
            <td class="small">{{ JSON.stringify(entry.summary) }}</td>
          </tr>
        </tbody>
      </table>
    </section>
  </main>
  <main v-else class="workspace">
    <p class="error">{{ error || "加载项目…" }}</p>
  </main>
</template>
