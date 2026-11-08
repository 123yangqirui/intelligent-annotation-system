<script setup lang="ts">
import { ref, onMounted, onUnmounted } from "vue";
import { api, post, jobHeaders } from "../api/http";
interface Run {
  id: string;
  instruction: string;
  status: string;
  questions: string[];
  summary: any;
  error: string | null;
  plan: any;
  tool_calls?: any[];
  skills?: any[];
}
const props = defineProps<{
  projectId: string;
  taskType: string;
  datasets: any[];
  models: any[];
  datasetId: string;
  modelId: string;
}>();
const configs = ref<{ id: string; name: string; engine: string }[]>([]),
  runs = ref<Run[]>([]),
  selected = ref<Run | null>(null);
const configId = ref(""),
  dataset = ref(props.datasetId),
  model = ref(props.modelId),
  maxItems = ref(100),
  instruction = ref(""),
  allowExport = ref(false),
  reply = ref(""),
  error = ref(""),
  notice = ref(""),
  busy = ref(false);
let timer: number | undefined,
  generation = 0,
  alive = true;
let pending: { fingerprint: string; key: string } | null = null,
  replyKey: { message: string; key: string } | null = null;
const states: Record<string, string> = {
  queued: "排队中",
  planning: "理解指令并规划",
  needs_input: "等待补充信息",
  executing: "执行工具",
  waiting_job: "等待后台任务",
  completed: "流程完成",
  partially_failed: "部分失败",
  failed: "失败",
  cancelled: "已取消",
};
const names: Record<string, string> = {
  create_prediction_job: "创建预标注任务",
  create_reviewed_export: "创建审核快照导出",
  get_prediction_summary: "汇总真实结果",
  get_project_context: "读取项目",
  get_label_schema: "读取规范",
  list_dataset_versions: "查询数据版本",
  list_compatible_models: "查询可用模型",
};
async function select(id: string) {
  const current = ++generation;
  const detail = await api<Run>(`/agent-runs/${id}`);
  if (alive && current === generation) selected.value = detail;
}
async function load() {
  configs.value = await api(`/projects/${props.projectId}/agent-configs`);
  runs.value = await api(`/projects/${props.projectId}/agent-runs`);
  if (!configs.value.some((config) => config.id === configId.value))
    configId.value = "";
  if (!configId.value && configs.value.length === 1)
    configId.value = configs.value[0]!.id;
  if (selected.value) await select(selected.value.id);
}
async function action(fn: () => Promise<void>) {
  if (busy.value) return;
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
async function start() {
  const body = {
    config_id: configId.value,
    instruction: instruction.value,
    dataset_id: dataset.value || null,
    model_id: model.value || null,
    max_items: maxItems.value,
    allowed_actions: allowExport.value
      ? ["prediction", "export", "summary"]
      : ["prediction", "summary"],
  };
  const fingerprint = JSON.stringify(body);
  if (!pending || pending.fingerprint !== fingerprint)
    pending = { fingerprint, key: jobHeaders()["Idempotency-Key"] };
  const result = await post<Run>(
    `/projects/${props.projectId}/agent-runs`,
    body,
    { "Idempotency-Key": pending.key },
  );
  selected.value = result;
  notice.value = "运行已创建，正在获取进度。";
  await select(result.id);
  await load();
  pending = null;
  notice.value = "运行已创建，请查看计划与任务进度。";
}
async function continueRun() {
  if (!selected.value) return;
  const id = selected.value.id;
  if (!replyKey || replyKey.message !== reply.value)
    replyKey = { message: reply.value, key: jobHeaders()["Idempotency-Key"] };
  await post(
    `/agent-runs/${id}/input`,
    { message: reply.value },
    { "Idempotency-Key": replyKey.key },
  );
  replyKey = null;
  reply.value = "";
  await select(id);
  await load();
}
onMounted(() => {
  action(load);
  timer = window.setInterval(async () => {
    if (busy.value) return;
    try {
      await load();
    } catch (e) {
      if (alive) error.value = (e as Error).message;
    }
  }, 2500);
});
onUnmounted(() => {
  alive = false;
  generation++;
  window.clearInterval(timer);
});
</script>
<template>
  <section class="agent-workspace">
    <form class="panel stack agent-composer" @submit.prevent="action(start)">
      <p class="kicker">自然语言任务助手</p>
      <h2>告诉系统你想完成什么</h2>
      <p class="muted small">
        Agent
        规划并调用现有标注工具。预测结果仍进入人工审核，执行进度来自真实后台任务。
      </p>
      <p v-if="!configs.length" class="notice">
        当前没有已授权的 Agent 配置。请在管理中心添加规划服务和 Agent
        配置，并授权给当前项目。
      </p>
      <div class="form-grid">
        <label
          >Agent 配置<select v-model="configId" required :disabled="busy">
            <option value="">选择配置</option>
            <option
              v-for="config in configs"
              :key="config.id"
              :value="config.id"
            >
              {{ config.name }} ·
              {{
                config.engine === "claude_sdk" ? "Claude SDK" : "HTTP 计划引擎"
              }}
            </option>
          </select></label
        ><label
          >数据版本（可选）<select v-model="dataset" :disabled="busy">
            <option value="">让 Agent 根据指令确定</option>
            <option
              v-for="d in datasets.filter((x) => x.status === 'ready')"
              :key="d.id"
              :value="d.id"
            >
              {{ d.name }}
            </option>
          </select></label
        ><label
          >标注模型（可选）<select v-model="model" :disabled="busy">
            <option value="">让 Agent 根据指令确定</option>
            <option
              v-for="m in models.filter((x) => x.task_type === taskType)"
              :key="m.id"
              :value="m.id"
            >
              {{ m.name }}
            </option>
          </select></label
        >
        <label
          >本次最多预标注的数量<input
            v-model.number="maxItems"
            type="number"
            min="1"
            max="500"
            :disabled="busy"
        /></label>
      </div>
      <label
        >任务指令<textarea
          v-model="instruction"
          rows="4"
          maxlength="6000"
          required
          :disabled="busy"
          :placeholder="
            taskType === 'text_classification'
              ? '用所选模型给该版本未开始的前 100 条文本预标注，并汇总人工复核原因。'
              : '用已部署的 YOLO 给所选版本前 100 张未标注图片预标注，完成后汇总低分和空预测。'
          "
        ></textarea></label
      ><label class="checkbox"
        ><input
          v-model="allowExport"
          type="checkbox"
          :disabled="busy"
        />允许本次运行导出已人工审核的数据</label
      ><button class="btn primary" :disabled="busy || !configId">
        创建 Agent 运行
      </button>
    </form>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="notice" class="notice">{{ notice }}</p>
    <div class="agent-results-layout">
      <section class="panel stack agent-history">
        <h2>运行历史</h2>
        <button
          v-for="run in runs"
          :key="run.id"
          class="agent-run-link"
          :class="{ selected: selected?.id === run.id }"
          @click="action(() => select(run.id))"
        >
          <strong>{{ states[run.status] || run.status }}</strong
          ><span>{{ run.instruction }}</span>
        </button>
        <p v-if="!runs.length" class="muted">还没有运行记录。</p>
      </section>
      <section v-if="selected" class="panel stack agent-detail">
        <div class="row-between">
          <h2>{{ states[selected.status] }}</h2>
          <button
            v-if="
              ![
                'completed',
                'failed',
                'partially_failed',
                'cancelled',
              ].includes(selected.status)
            "
            class="btn"
            :disabled="busy"
            @click="
              action(async () => {
                await post(`/agent-runs/${selected!.id}/cancel`);
                await load();
              })
            "
          >
            取消运行及关联任务
          </button>
        </div>
        <p class="muted small">{{ selected.instruction }}</p>
        <p v-if="selected.error" class="error">{{ selected.error }}</p>
        <form
          v-if="selected.status === 'needs_input'"
          @submit.prevent="action(continueRun)"
        >
          <p
            v-for="question in selected.questions"
            :key="question"
            class="notice"
          >
            {{ question }}
          </p>
          <label
            >补充信息<textarea
              v-model="reply"
              required
              maxlength="4000"
              :disabled="busy"
              placeholder="例如：使用数据版本 2，模型选择 BERT 分类服务。"
            ></textarea></label
          ><button class="btn primary" :disabled="busy">补充并继续</button>
        </form>
        <div v-if="selected.plan?.steps?.length">
          <h3>执行计划</h3>
          <ol>
            <li v-for="(step, index) in selected.plan.steps" :key="index">
              {{
                step.action === "prediction"
                  ? "预标注"
                  : step.action === "export"
                    ? "导出已审核结果"
                    : "汇总结果"
              }}<span v-if="step.action === 'prediction'">
                · 最多 {{ step.limit }} 条</span
              >
            </li>
          </ol>
        </div>
        <p v-if="selected.summary?.message" class="notice">
          {{ selected.summary.message }}
        </p>
        <p v-if="selected.summary?.predicted !== undefined" class="small">
          已生成 {{ selected.summary.predicted }} 条预测 · 失败
          {{ selected.summary.failed_items }} 条 ·
          {{ selected.summary.review_policy }}
        </p>
        <p v-if="selected.summary?.review_reasons" class="muted small">
          重点复核：低分
          {{ selected.summary.review_reasons.low_model_score || 0 }} · 空预测
          {{
            selected.summary.review_reasons
              .empty_prediction_requires_image_review || 0
          }}
        </p>
        <h3 v-if="selected.tool_calls?.length">工具执行记录</h3>
        <article
          v-for="call in selected.tool_calls"
          :key="call.id"
          class="revision-card"
        >
          <div class="row-between">
            <strong class="small">{{ names[call.name] || call.name }}</strong
            ><span class="badge">{{
              call.status === "succeeded"
                ? "成功"
                : call.status === "waiting_job"
                  ? "等待任务"
                  : call.status === "failed"
                    ? "失败"
                    : "待执行"
            }}</span>
          </div>
          <p v-if="call.error" class="error">{{ call.error }}</p>
          <p v-if="call.child_job_id" class="muted small">
            任务 {{ call.child_job_id.slice(0, 8) }} · {{ call.result?.status }}
          </p>
        </article>
        <p class="muted small" v-if="selected.skills?.length">
          本次技能：{{
            selected.skills.map((x) => `${x.name} v${x.version}`).join("、")
          }}
        </p>
      </section>
      <section v-else class="panel empty muted">
        选择一条运行，查看计划、补充问题和结果。
      </section>
    </div>
  </section>
</template>
