<script setup lang="ts">
import { computed, ref } from "vue";
import {
  BACKEND_DOCS_URL,
  HEALTH_ENDPOINT,
  checkBackendHealth,
  type HealthResponse,
} from "./api/client";

type ConnectionState = "idle" | "loading" | "success" | "error";

// ref 保存页面状态，值改变时 Vue 会自动更新对应的界面。
const state = ref<ConnectionState>("idle");
const responseData = ref<HealthResponse | null>(null);
const errorMessage = ref("");
const elapsedMs = ref<number | null>(null);
const checkedAt = ref("");

const stateLabels: Record<ConnectionState, string> = {
  idle: "尚未检查",
  loading: "正在连接",
  success: "连接成功",
  error: "连接失败",
};

const statusLabel = computed(() => stateLabels[state.value]);
const responseText = computed(() =>
  JSON.stringify(responseData.value, null, 2),
);

async function handleCheck() {
  if (state.value === "loading") return;

  state.value = "loading";
  responseData.value = null;
  errorMessage.value = "";
  elapsedMs.value = null;
  checkedAt.value = "";
  const startedAt = performance.now();

  try {
    responseData.value = await checkBackendHealth();
    state.value = "success";
  } catch (error) {
    errorMessage.value =
      error instanceof Error ? error.message : "检查失败，请稍后重试。";
    state.value = "error";
  } finally {
    elapsedMs.value = Math.round(performance.now() - startedAt);
    checkedAt.value = new Date().toLocaleTimeString("zh-CN", {
      hour12: false,
      timeZone: "Asia/Shanghai",
    });
  }
}
</script>

<template>
  <div class="page-shell">
    <header class="site-header">
      <a class="brand" href="/" aria-label="智能标注系统首页">
        <span class="brand-mark" aria-hidden="true">标</span>
        <span>智能标注系统</span>
      </a>
      <span class="environment-badge"
        ><span class="environment-dot"></span>本地开发</span
      >
    </header>

    <main class="main-content">
      <div class="page-intro">
        <p class="eyebrow">阶段 01 · 前后端连接</p>
        <h1>后端连接检查</h1>
        <p class="intro-description">
          发送一次请求，确认 Vue 页面能够连接到 FastAPI 服务。
        </p>
      </div>

      <section
        class="connection-card"
        aria-labelledby="connection-heading"
        :aria-busy="state === 'loading'"
      >
        <div class="card-topline">
          <h2 id="connection-heading">服务状态</h2>
          <span class="status-badge" :class="state" role="status">
            <span class="status-dot" aria-hidden="true"></span>{{ statusLabel }}
          </span>
        </div>

        <div class="endpoint-row">
          <span class="http-method">GET</span>
          <code>{{ HEALTH_ENDPOINT }}</code>
        </div>
        <p class="endpoint-description">健康检查范围：FastAPI 接口响应。</p>

        <div class="actions">
          <button
            class="primary-button"
            :disabled="state === 'loading'"
            @click="handleCheck"
          >
            <span
              v-if="state === 'loading'"
              class="spinner"
              aria-hidden="true"
            ></span>
            <span v-else aria-hidden="true">↗</span>
            {{ state === "loading" ? "检查中…" : "检查后端连接" }}
          </button>
          <a
            class="documentation-link"
            :href="BACKEND_DOCS_URL"
            target="_blank"
            rel="noopener noreferrer"
          >
            打开 API 文档 <span aria-hidden="true">↗</span>
          </a>
        </div>

        <p v-if="state === 'error'" class="error-message" role="alert">
          {{ errorMessage }}
        </p>
        <p v-else-if="state === 'success'" class="success-message">
          已收到有效响应，前后端连接正常。
        </p>
        <p v-else class="helper-message">
          {{
            state === "loading"
              ? "正在等待响应，最长等待 5 秒。"
              : "请先启动后端，再点击按钮检查连接。"
          }}
        </p>

        <dl class="request-metrics">
          <div>
            <dt>响应耗时</dt>
            <dd>{{ elapsedMs === null ? "—" : `${elapsedMs} ms` }}</dd>
          </div>
          <div>
            <dt>检查时间（上海）</dt>
            <dd>{{ checkedAt || "—" }}</dd>
          </div>
        </dl>
      </section>

      <div class="detail-grid">
        <section class="response-card" aria-labelledby="response-heading">
          <div class="response-header">
            <h2 id="response-heading">接口响应</h2>
            <span>JSON</span>
          </div>
          <pre v-if="responseData"><code>{{ responseText }}</code></pre>
          <div v-else class="response-placeholder">
            <span aria-hidden="true">{ }</span>
            <p>
              {{
                state === "error"
                  ? "本次未收到有效的健康检查响应"
                  : "检查成功后，响应数据会显示在这里"
              }}
            </p>
          </div>
        </section>

        <section class="flow-card" aria-labelledby="flow-heading">
          <h2 id="flow-heading">这次请求如何完成？</h2>
          <ol class="request-flow">
            <li>
              <span class="step-number">1</span>
              <div>
                <h3>页面发送请求</h3>
                <p>点击按钮，调用健康检查接口。</p>
              </div>
            </li>
            <li>
              <span class="step-number">2</span>
              <div>
                <h3>开发代理转发</h3>
                <p>Vite 将 /api 请求转交给 FastAPI。</p>
              </div>
            </li>
            <li>
              <span class="step-number">3</span>
              <div>
                <h3>响应更新页面</h3>
                <p>检查返回数据，显示状态与结果。</p>
              </div>
            </li>
          </ol>
        </section>
      </div>

      <footer class="page-footer">
        <span>Vue 3 · TypeScript · FastAPI</span
        ><span>从一个可运行的功能开始</span>
      </footer>
    </main>
  </div>
</template>
