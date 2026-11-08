<script setup lang="ts">
import { ref, onMounted, watch } from "vue";
import { api, post } from "../api/http";
defineProps<{ providers: any[] }>();
const configs = ref<any[]>([]),
  skills = ref<any[]>([]),
  projects = ref<any[]>([]),
  error = ref(""),
  notice = ref(""),
  busy = ref(false);
const editing = ref(""),
  grantProject = ref(""),
  grantConfig = ref("");
const defaults = () => ({
  name: "",
  engine: "claude_sdk",
  protocol: "anthropic",
  provider_id: "",
  system_prompt: "",
  skill_ids: [] as string[],
  enabled: true,
  max_steps: 8,
  max_turns: 6,
  max_output_tokens: 2048,
  timeout_seconds: 90,
});
const form = ref(defaults()),
  skillEdit = ref(""),
  skillForm = ref({ name: "", description: "", content: "", enabled: true });
watch(
  () => form.value.engine,
  (value) => {
    if (value === "claude_sdk") form.value.protocol = "anthropic";
  },
);
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
async function load() {
  [configs.value, skills.value, projects.value] = await Promise.all([
    api("/admin/agent-configs"),
    api("/admin/agent-skills"),
    api("/projects"),
  ]);
}
function edit(config: any) {
  editing.value = config.id;
  form.value = Object.fromEntries(
    Object.keys(defaults()).map((key) => [key, config[key]]),
  ) as ReturnType<typeof defaults>;
  form.value.skill_ids = [...config.skill_ids];
}
async function save() {
  if (editing.value)
    await api(`/admin/agent-configs/${editing.value}`, {
      method: "PUT",
      body: JSON.stringify(form.value),
    });
  else await post("/admin/agent-configs", form.value);
  await load();
  notice.value = "Agent 配置已保存；运行会固定配置与技能版本。";
}
async function saveSkill() {
  if (skillEdit.value)
    await api(`/admin/agent-skills/${skillEdit.value}`, {
      method: "PUT",
      body: JSON.stringify(skillForm.value),
    });
  else await post("/admin/agent-skills", skillForm.value);
  await load();
  notice.value = "技能已保存；已有运行保留原版本快照。";
}
async function grant() {
  const config = configs.value.find((x) => x.id === grantConfig.value);
  await post(
    `/admin/providers/${config.provider_id}/grant?project_id=${grantProject.value}`,
  );
  notice.value = "规划服务已授权给项目。";
}
onMounted(() => action(load));
</script>
<template>
  <section>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="notice" class="notice">{{ notice }}</p>
    <div class="two-columns">
      <form class="panel stack" @submit.prevent="action(save)">
        <div class="row-between">
          <h2>{{ editing ? "编辑 Agent 配置" : "新增 Agent 配置" }}</h2>
          <button
            type="button"
            class="quiet"
            @click="
              editing = '';
              form = defaults();
            "
          >
            新建
          </button>
        </div>
        <p class="muted small">
          先在“大模型 API”配置规划服务。Claude SDK 使用 Anthropic 协议，HTTP
          引擎使用选定协议。标注模型在项目中另行选择。
        </p>
        <label
          >配置名称<input v-model="form.name" required maxlength="100"
        /></label>
        <div class="form-grid">
          <label
            >引擎<select v-model="form.engine">
              <option value="claude_sdk">Claude Agent SDK</option>
              <option value="http_plan">HTTP 结构化计划</option>
            </select></label
          ><label
            >服务协议<select
              v-model="form.protocol"
              :disabled="form.engine === 'claude_sdk'"
            >
              <option value="anthropic">Anthropic Messages</option>
              <option value="openai">OpenAI Chat Completions</option>
            </select></label
          >
        </div>
        <label
          >规划服务<select v-model="form.provider_id" required>
            <option value="">选择供应商配置</option>
            <option v-for="p in providers" :key="p.id" :value="p.id">
              {{ p.name }} · {{ p.model_name }}
            </option>
          </select></label
        ><label
          >追加系统提示词<textarea
            v-model="form.system_prompt"
            rows="4"
            maxlength="12000"
            placeholder="系统权限和工具限制仍由后端保证。"
          ></textarea>
        </label>
        <fieldset class="skill-list">
          <legend>启用技能</legend>
          <label
            v-for="skill in skills.filter((x) => x.enabled)"
            :key="skill.id"
            class="checkbox"
            ><input
              type="checkbox"
              v-model="form.skill_ids"
              :value="skill.id"
            />{{ skill.description }} · v{{ skill.version }}</label
          >
        </fieldset>
        <div class="form-grid">
          <label
            >最大计划步骤<input
              type="number"
              v-model.number="form.max_steps"
              min="1"
              max="8" /></label
          ><label
            >SDK 最大轮数<input
              type="number"
              v-model.number="form.max_turns"
              min="1"
              max="12" /></label
          ><label
            >最大输出 token<input
              type="number"
              v-model.number="form.max_output_tokens"
              min="256"
              max="8192" /></label
          ><label
            >规划超时（秒）<input
              type="number"
              v-model.number="form.timeout_seconds"
              min="10"
              max="180"
          /></label>
        </div>
        <p class="muted small">
          SDK 会保守预留多轮规划用量，规划供应商需设置足够的 token 预算。
        </p>
        <label class="checkbox"
          ><input type="checkbox" v-model="form.enabled" />启用配置</label
        ><button class="btn primary" :disabled="busy">保存配置</button>
      </form>
      <div>
        <section class="panel stack">
          <h2>已注册配置</h2>
          <article
            v-for="config in configs"
            :key="config.id"
            class="revision-card"
          >
            <div class="row-between">
              <strong>{{ config.name }}</strong
              ><button class="quiet" @click="edit(config)">编辑</button>
            </div>
            <p class="muted small">
              {{ config.engine }} · {{ config.protocol }} ·
              {{ config.enabled ? "已启用" : "已停用" }}
            </p>
          </article>
          <p v-if="!configs.length" class="muted">尚未注册 Agent。</p>
        </section>
        <form class="panel stack" @submit.prevent="action(grant)">
          <h2>授权给项目</h2>
          <label
            >Agent 配置<select v-model="grantConfig" required>
              <option
                v-for="config in configs"
                :key="config.id"
                :value="config.id"
              >
                {{ config.name }}
              </option>
            </select></label
          ><label
            >项目<select v-model="grantProject" required>
              <option v-for="p in projects" :key="p.id" :value="p.id">
                {{ p.name }}
              </option>
            </select></label
          ><button class="btn" :disabled="busy">授权规划服务</button>
        </form>
      </div>
    </div>
    <section class="panel stack">
      <div class="row-between">
        <h2>技能管理</h2>
        <button
          class="btn"
          :disabled="busy"
          @click="
            action(async () => {
              await post('/admin/agent-skills/seed');
              await load();
              notice = '内置技能已初始化。';
            })
          "
        >
          初始化内置技能
        </button>
      </div>
      <div class="chips">
        <button
          v-for="skill in skills"
          :key="skill.id"
          class="btn"
          @click="
            skillEdit = skill.id;
            skillForm = {
              name: skill.name,
              description: skill.description,
              content: skill.content,
              enabled: skill.enabled,
            };
          "
        >
          {{ skill.name }} v{{ skill.version }}</button
        ><button
          class="quiet"
          @click="
            skillEdit = '';
            skillForm = {
              name: '',
              description: '',
              content: '',
              enabled: true,
            };
          "
        >
          新增技能
        </button>
      </div>
      <form @submit.prevent="action(saveSkill)">
        <div class="form-grid">
          <label
            >名称<input
              v-model="skillForm.name"
              required
              pattern="[a-z][a-z0-9_-]{1,63}"
              placeholder="image-preannotation" /></label
          ><label
            >说明<input
              v-model="skillForm.description"
              required
              maxlength="500"
          /></label>
        </div>
        <label
          >Markdown 正文<textarea
            v-model="skillForm.content"
            rows="7"
            required
            maxlength="12000"
            placeholder="只填写技能步骤；系统生成名称与说明的 frontmatter。"
          ></textarea></label
        ><label class="checkbox"
          ><input type="checkbox" v-model="skillForm.enabled" />启用技能</label
        ><button class="btn primary" :disabled="busy">
          {{ skillEdit ? "保存新版本" : "创建技能" }}
        </button>
      </form>
    </section>
  </section>
</template>
