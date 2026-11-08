<script setup lang="ts">
import { ref, onMounted } from "vue";
import { api, post, jobHeaders } from "../api/http";
import AgentSettings from "../components/AgentSettings.vue";
import AppIcon from "../components/AppIcon.vue";
const tab = ref("users");
const users = ref<any[]>([]);
const providers = ref<any[]>([]);
const models = ref<any[]>([]);
const deployments = ref<any[]>([]);
const events = ref<any[]>([]);
const error = ref("");
const notice = ref("");
const busy = ref(false);
const username = ref("");
const password = ref("");
const isAdmin = ref(false);
const providerName = ref("");
const baseUrl = ref("");
const modelName = ref("");
const apiKey = ref("");
const budget = ref(100000);
const projectId = ref("");
const grantId = ref("");
const specName = ref("");
const adapter = ref("llm");
const taskType = ref("text_classification");
const providerId = ref("");
const endpoint = ref("");
const deploymentModel = ref("");
const template = ref("bert");
const checkpoint = ref("");
const port = ref(8101);
const manageId = ref("");
const manageBudget = ref(100000);
const manageEnabled = ref(true);
const rotateKey = ref("");
const calls = ref<any[]>([]);
const callId = ref("");
const actualTokens = ref(0);
const system = ref<Record<string, unknown>>({});
const systemServices = [
  { key: "postgresql", name: "业务数据库", detail: "PostgreSQL" },
  { key: "mongodb", name: "执行事件", detail: "MongoDB" },
  { key: "redis_broker", name: "任务队列", detail: "Redis" },
  { key: "redis_cache", name: "预测缓存", detail: "Redis" },
  { key: "docker_available", name: "模型运行环境", detail: "Docker" },
];
async function load() {
  [users.value, providers.value, models.value, deployments.value] =
    await Promise.all([
      api("/admin/users"),
      api("/admin/providers"),
      api("/models"),
      api("/admin/deployments"),
    ]);
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
async function createUser() {
  await action(async () => {
    await post("/admin/users", {
      username: username.value,
      password: password.value,
      is_admin: isAdmin.value,
    });
    password.value = "";
    await load();
    notice.value = "账号已创建。";
  });
}
async function createProvider() {
  await action(async () => {
    await post("/admin/providers", {
      name: providerName.value,
      base_url: baseUrl.value,
      model_name: modelName.value,
      api_key: apiKey.value,
      budget_tokens: budget.value,
    });
    apiKey.value = "";
    await load();
    notice.value = "配置已保存，密钥已加密。";
  });
}
async function createModel() {
  await action(async () => {
    await post("/admin/models", {
      name: specName.value,
      adapter: adapter.value,
      task_type: taskType.value,
      provider_id: adapter.value === "llm" ? providerId.value : null,
      config: adapter.value === "llm" ? {} : { endpoint: endpoint.value },
    });
    await load();
  });
}
async function deploy() {
  await action(async () => {
    await post(
      "/admin/deployments",
      {
        model_id: deploymentModel.value,
        template: template.value,
        checkpoint: checkpoint.value,
        port: port.value,
      },
      jobHeaders(),
    );
    await load();
    notice.value = "部署任务已提交，请查看状态与任务日志。";
  });
}
onMounted(() => action(load));
</script>
<template>
  <main class="workspace">
    <div class="page-title">
      <div>
        <p class="kicker">系统管理</p>
        <h1>管理中心</h1>
        <p class="muted">账号、模型服务、可插拔模型与部署。</p>
      </div>
      <button class="btn" @click="action(load)">刷新</button>
    </div>
    <div class="admin-layout">
      <nav class="admin-navigation" aria-label="管理模块">
        <p class="nav-heading">系统设置</p>
        <button
          v-for="[key, title] in [
            ['users', '用户'],
            ['providers', '大模型 API'],
            ['models', '模型注册'],
            ['deployments', '模型部署'],
            ['events', '执行事件'],
            ['system', '运行状态'],
            ['agent', 'Agent 配置与技能'],
          ]"
          :key="key"
          :class="{ active: tab === key }"
          :aria-current="tab === key ? 'page' : undefined"
          @click="tab = key!"
        >
          {{ title }}
        </button>
      </nav>
      <div class="admin-content">
        <p class="error" v-if="error" role="alert">{{ error }}</p>
        <p class="notice" v-if="notice">{{ notice }}</p>
        <AgentSettings v-if="tab === 'agent'" :providers="providers" />
        <section v-if="tab === 'users'" class="two-columns user-management">
          <form class="panel" @submit.prevent="createUser">
            <h2>创建用户</h2>
            <label>用户名<input v-model="username" required /></label
            ><label
              >初始密码<input
                v-model="password"
                type="password"
                minlength="8"
                autocomplete="new-password"
                required /></label
            ><label class="checkbox"
              ><input v-model="isAdmin" type="checkbox" />全局管理员</label
            ><button class="btn primary" :disabled="busy">创建账号</button>
          </form>
          <div class="panel">
            <h2>
              用户列表 <span class="count-pill">{{ users.length }}</span>
            </h2>
            <table>
              <thead>
                <tr>
                  <th>用户</th>
                  <th>身份</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="u in users" :key="u.id">
                  <td>{{ u.username }}</td>
                  <td>{{ u.is_admin ? "管理员" : "普通用户" }}</td>
                  <td>
                    <button
                      class="quiet"
                      @click="
                        action(async () => {
                          await api(
                            `/admin/users/${u.id}?enabled=${!u.enabled}`,
                            {
                              method: 'PATCH',
                            },
                          );
                          await load();
                        })
                      "
                    >
                      {{ u.enabled ? "禁用" : "启用" }}
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
        <section v-if="tab === 'providers'" class="two-columns">
          <form class="panel" @submit.prevent="createProvider">
            <h2>添加大模型服务</h2>
            <label>配置名称<input v-model="providerName" required /></label
            ><label
              >API 地址<input
                v-model="baseUrl"
                required
                placeholder="https://供应商地址/v1" /></label
            ><label>模型名称<input v-model="modelName" required /></label
            ><label
              >API Key<input
                v-model="apiKey"
                type="password"
                autocomplete="off"
                required /></label
            ><label
              >Token 总预算<input
                v-model.number="budget"
                type="number"
                min="1" /></label
            ><button class="btn primary" :disabled="busy">保存配置</button>
          </form>
          <div>
            <article
              class="panel stack"
              v-for="provider in providers"
              :key="provider.id"
            >
              <h2>{{ provider.name }}</h2>
              <p class="muted small">
                {{ provider.model_name }} · {{ provider.base_url }}
              </p>
              <p class="small">
                已用 {{ provider.spent_tokens }} · 预留
                {{ provider.reserved_tokens }} /
                {{ provider.budget_tokens }} tokens
              </p>
              <button
                class="btn"
                @click="
                  action(async () => {
                    const result: any = await post(
                      `/admin/providers/${provider.id}/test`,
                    );
                    notice = JSON.stringify(result);
                  })
                "
              >
                测试连接
              </button>
            </article>
            <section class="panel">
              <h2>授权给项目</h2>
              <label
                >服务<select v-model="grantId">
                  <option
                    v-for="provider in providers"
                    :key="provider.id"
                    :value="provider.id"
                  >
                    {{ provider.name }}
                  </option>
                </select></label
              ><label>项目 ID<input v-model="projectId" /></label
              ><button
                class="btn"
                @click="
                  action(async () => {
                    await post(
                      `/admin/providers/${grantId}/grant?project_id=${projectId}`,
                    );
                    notice = '项目授权已保存。';
                  })
                "
              >
                授权
              </button>
            </section>
          </div>
        </section>
        <section v-if="tab === 'providers'" class="two-columns">
          <form
            class="panel"
            @submit.prevent="
              action(async () => {
                await api(`/admin/providers/${manageId}`, {
                  method: 'PATCH',
                  body: JSON.stringify({
                    budget_tokens: manageBudget,
                    enabled: manageEnabled,
                  }),
                });
                await load();
                notice = '配置已更新。';
              })
            "
          >
            <h2>管理已有配置</h2>
            <label
              >供应商<select v-model="manageId" required>
                <option
                  v-for="provider in providers"
                  :key="provider.id"
                  :value="provider.id"
                >
                  {{ provider.name }}
                </option>
              </select></label
            ><label
              >Token 预算<input
                v-model.number="manageBudget"
                type="number"
                min="1" /></label
            ><label class="checkbox"
              ><input v-model="manageEnabled" type="checkbox" />启用服务</label
            ><button class="btn">更新预算与状态</button
            ><label
              >新 API Key<input
                v-model="rotateKey"
                type="password"
                autocomplete="off" /></label
            ><button
              type="button"
              class="btn"
              :disabled="!manageId || !rotateKey"
              @click="
                action(async () => {
                  await post(`/admin/providers/${manageId}/rotate`, {
                    api_key: rotateKey,
                  });
                  rotateKey = '';
                  notice = '密钥已轮换。';
                })
              "
            >
              轮换密钥
            </button>
          </form>
          <section class="panel">
            <div class="row-between">
              <h2>未知用量对账</h2>
              <button
                class="btn"
                @click="
                  action(async () => {
                    calls = await api('/admin/model-calls');
                  })
                "
              >
                读取调用账本
              </button>
            </div>
            <p class="muted small">
              填写供应商确认的实际 token
              用量，释放对应预留。不会自动假定超时未计费。
            </p>
            <label
              >待对账调用<select v-model="callId">
                <option
                  v-for="call in calls.filter(
                    (x) => x.status === 'usage_unknown',
                  )"
                  :key="call.id"
                  :value="call.id"
                >
                  {{ call.id }} · 预留 {{ call.reserved }}
                </option>
              </select></label
            ><label
              >实际总 token<input
                v-model.number="actualTokens"
                type="number"
                min="0" /></label
            ><button
              class="btn"
              :disabled="!callId"
              @click="
                action(async () => {
                  await post(
                    `/admin/model-calls/${callId}/reconcile?total_tokens=${actualTokens}`,
                  );
                  calls = await api('/admin/model-calls');
                  await load();
                  notice = '对账已保存。';
                })
              "
            >
              确认对账
            </button>
          </section>
        </section>

        <section v-if="tab === 'models'" class="two-columns">
          <form class="panel" @submit.prevent="createModel">
            <h2>注册模型规格</h2>
            <label>显示名称<input v-model="specName" required /></label
            ><label
              >适配器<select v-model="adapter">
                <option value="llm">语言模型 API</option>
                <option value="bert">BERT 分类服务</option>
                <option value="yolo">YOLO 检测服务</option>
                <option value="http">标准 HTTP 模型服务</option>
              </select></label
            ><label
              >任务类型<select v-model="taskType">
                <option value="text_classification">文本分类</option>
                <option value="image_detection">图像检测</option>
              </select></label
            ><label v-if="adapter === 'llm'"
              >供应商<select v-model="providerId" required>
                <option
                  v-for="provider in providers"
                  :key="provider.id"
                  :value="provider.id"
                >
                  {{ provider.name }}
                </option>
              </select></label
            ><label v-else
              >服务地址<input
                v-model="endpoint"
                required
                placeholder="http://127.0.0.1:8101" /></label
            ><button class="btn primary" :disabled="busy">注册新版本</button>
          </form>
          <div>
            <article
              class="panel stack"
              v-for="model in models"
              :key="model.id"
            >
              <h2>{{ model.name }}</h2>
              <p class="muted small">
                {{ model.adapter }} · {{ model.task_type }}
              </p>
              <code class="small">{{ model.id }}</code>
            </article>
          </div>
        </section>
        <section v-if="tab === 'deployments'" class="two-columns">
          <form class="panel" @submit.prevent="deploy">
            <h2>启动模型服务</h2>
            <p class="muted small">
              使用固定 Docker 模板。语言模型模板需要 Linux GPU
              环境；资源不足会明确失败。
            </p>
            <label
              >模型规格<select v-model="deploymentModel" required>
                <option
                  v-for="model in models"
                  :value="model.id"
                  :key="model.id"
                >
                  {{ model.name }}
                </option>
              </select></label
            ><label
              >模板<select v-model="template">
                <option>bert</option>
                <option>yolo</option>
                <option>qwen</option>
                <option>deepseek</option>
              </select></label
            ><label
              >检查点<input
                v-model="checkpoint"
                required
                placeholder="组织/模型名称 或 YOLO 权重名" /></label
            ><label
              >服务端口<input
                v-model.number="port"
                type="number"
                min="8101"
                max="8199" /></label
            ><button class="btn primary" :disabled="busy">提交部署任务</button>
          </form>
          <div>
            <article
              v-for="deployment in deployments"
              :key="deployment.id"
              class="panel stack"
            >
              <div class="row-between">
                <h2>{{ deployment.config.template }}</h2>
                <span class="badge">{{ deployment.status }}</span>
              </div>
              <p class="small">
                {{ deployment.config.checkpoint }} ·
                {{ deployment.config.port }}
              </p>
              <p class="error" v-if="deployment.error">
                {{ deployment.error }}
              </p>
              <button
                class="btn"
                @click="
                  action(async () => {
                    await post(
                      `/admin/deployments/${deployment.id}/stop`,
                      undefined,
                      jobHeaders(),
                    );
                    await load();
                  })
                "
              >
                停止服务
              </button>
            </article>
          </div>
        </section>
        <section v-if="tab === 'events'" class="panel">
          <div class="row-between">
            <h2>MongoDB 执行事件</h2>
            <button
              class="btn"
              @click="
                action(async () => {
                  events = await api('/admin/events');
                })
              "
            >
              读取事件
            </button>
          </div>
          <pre>{{ JSON.stringify(events, null, 2) }}</pre>
        </section>
        <section v-if="tab === 'system'" class="panel">
          <div class="row-between">
            <h2>基础设施状态</h2>
            <button
              class="btn"
              :disabled="busy"
              @click="
                action(async () => {
                  system = await api('/admin/system');
                })
              "
            >
              检查服务
            </button>
          </div>
          <p class="muted small">检查数据库、队列与模型运行环境的连接状态。</p>
          <div v-if="Object.keys(system).length" class="system-status-grid">
            <article v-for="service in systemServices" :key="service.key">
              <div class="row-between">
                <AppIcon name="server" /><span
                  class="badge"
                  :class="system[service.key] ? 'success' : 'pending'"
                  >{{ system[service.key] ? "正常" : "需要检查" }}</span
                >
              </div>
              <h3>{{ service.name }}</h3>
              <p class="muted small">{{ service.detail }}</p>
            </article>
            <article>
              <h3>后台任务</h3>
              <div class="queue-counts">
                <div>
                  <strong>{{ system.jobs_queued }}</strong
                  ><span>排队中</span>
                </div>
                <div>
                  <strong>{{ system.outbox_pending }}</strong
                  ><span>待派发事件</span>
                </div>
              </div>
            </article>
          </div>
          <p v-else class="empty muted">点击“检查服务”获取当前状态。</p>
          <details v-if="Object.keys(system).length" class="settings-details">
            <summary>查看技术信息</summary>
            <pre>{{ JSON.stringify(system, null, 2) }}</pre>
          </details>
        </section>
      </div>
    </div>
  </main>
</template>
