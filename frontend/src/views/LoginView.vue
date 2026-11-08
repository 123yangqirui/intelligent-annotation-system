<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { login } from "../api/http";
const router = useRouter();
const username = ref("");
const password = ref("");
const error = ref("");
const busy = ref(false);
async function submit() {
  busy.value = true;
  error.value = "";
  try {
    await login(username.value, password.value);
    router.push("/");
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <main class="login-layout">
    <div class="login-intro">
      <p class="kicker">ANNOTATION WORKSPACE</p>
      <h1>让数据成为<br />可靠的训练素材。</h1>
      <p>
        文本分类与图像检测，从预标注到审核导出，<br />在一个工作空间中完成。
      </p>
      <div class="login-tags">
        <span>文本标注</span><span>目标检测</span><span>版本追踪</span>
      </div>
    </div>
    <form class="panel login-panel" @submit.prevent="submit">
      <p class="kicker">欢迎回来</p>
      <h2>登录工作空间</h2>
      <label
        >用户名<input
          v-model="username"
          autocomplete="username"
          required
          placeholder="输入管理员分配的用户名" /></label
      ><label
        >密码<input
          v-model="password"
          type="password"
          autocomplete="current-password"
          required
          placeholder="输入密码"
      /></label>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <button :disabled="busy" class="btn primary">
        {{ busy ? "登录中…" : "登录" }}
      </button>
      <p class="muted small">
        账号由管理员创建。首次使用请按运行说明初始化管理员。
      </p>
    </form>
  </main>
</template>
