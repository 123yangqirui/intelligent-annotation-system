<script setup lang="ts">
import { RouterLink, RouterView, useRouter, useRoute } from "vue-router";
import { ref, computed, watch } from "vue";
import { user, logout, post } from "./api/http";
import AppIcon from "./components/AppIcon.vue";
import ModalDialog from "./components/ModalDialog.vue";
const router = useRouter();
const route = useRoute();
const menuOpen = ref(false);
const focusWorkspace = computed(() => route.path.startsWith("/annotate/"));
const withSidebar = computed(
  () => !!user.value && !focusWorkspace.value && route.path !== "/login",
);
const contextTitle = computed(() =>
  route.path === "/login"
    ? "登录工作空间"
    : route.path.startsWith("/admin")
      ? "系统管理"
      : route.path.startsWith("/health")
        ? "连接检查"
        : focusWorkspace.value
          ? "标注工作台"
          : route.path.startsWith("/projects/")
            ? "项目工作空间"
            : "我的项目",
);
watch(
  () => route.path,
  () => {
    menuOpen.value = false;
  },
);
const showPassword = ref(false);
const currentPassword = ref("");
const newPassword = ref("");
const passwordError = ref("");
async function signOut() {
  await logout();
  router.push("/login");
}
async function changePassword() {
  try {
    await post("/auth/password", {
      current_password: currentPassword.value,
      new_password: newPassword.value,
    });
    currentPassword.value = "";
    newPassword.value = "";
    showPassword.value = false;
    await signOut();
  } catch (e) {
    passwordError.value = (e as Error).message;
  }
}
</script>
<template>
  <div
    class="application"
    :class="{
      'with-sidebar': withSidebar,
      'focus-workspace': focusWorkspace,
      'menu-open': menuOpen,
    }"
  >
    <aside v-if="withSidebar" class="app-sidebar" id="main-navigation">
      <RouterLink to="/" class="app-brand"
        ><span>标</span>
        <div>智能标注<small>文本与图像工作空间</small></div></RouterLink
      >
      <p class="nav-heading">工作空间</p>
      <nav aria-label="主导航">
        <RouterLink
          to="/"
          :class="{
            selected: route.path === '/' || route.path.startsWith('/projects/'),
          }"
          ><AppIcon name="projects" /><span>我的项目</span></RouterLink
        >
        <RouterLink v-if="user?.is_admin" to="/admin"
          ><AppIcon name="settings" /><span>管理中心</span></RouterLink
        >
        <RouterLink to="/health"
          ><AppIcon name="server" /><span>连接检查</span></RouterLink
        >
      </nav>
      <div class="sidebar-note">
        <span class="sidebar-note-mark">从数据到成果</span>
        <p>导入数据，完成标注，<br />审核后导出可信成果。</p>
      </div>
      <div class="sidebar-footer">
        <span class="avatar">{{
          user?.username.slice(0, 1).toUpperCase()
        }}</span>
        <div>
          <strong>{{ user?.username }}</strong
          ><small>{{ user?.is_admin ? "系统管理员" : "项目成员" }}</small>
        </div>
      </div>
    </aside>
    <button
      v-if="withSidebar && menuOpen"
      class="nav-scrim"
      aria-label="关闭导航"
      @click="menuOpen = false"
    ></button>
    <header class="app-header">
      <button
        v-if="withSidebar"
        class="icon-button mobile-menu"
        aria-label="切换导航"
        aria-controls="main-navigation"
        :aria-expanded="menuOpen"
        @click="menuOpen = !menuOpen"
      >
        <AppIcon name="menu" />
      </button>
      <RouterLink v-if="!withSidebar" to="/" class="app-brand"
        ><span>标</span>智能标注系统</RouterLink
      >
      <div class="header-context">
        <span>智能标注平台</span><span class="breadcrumb-divider">/</span
        ><strong>{{ contextTitle }}</strong>
      </div>
      <div class="account" v-if="user">
        <span>{{ user.username }}</span
        ><button class="quiet" @click="showPassword = !showPassword">
          修改密码</button
        ><button class="quiet" @click="signOut">退出</button>
      </div>
    </header>
    <ModalDialog
      v-if="showPassword"
      labelledby="password-title"
      @close="showPassword = false"
    >
      <form class="panel modal-panel" @submit.prevent="changePassword">
        <div class="row-between">
          <h2 id="password-title">修改密码</h2>
          <button
            type="button"
            class="icon-button"
            aria-label="关闭修改密码"
            @click="showPassword = false"
          >
            <AppIcon name="close" />
          </button>
        </div>
        <label
          >当前密码<input
            v-model="currentPassword"
            type="password"
            autocomplete="current-password"
            required /></label
        ><label
          >新密码<input
            v-model="newPassword"
            type="password"
            autocomplete="new-password"
            minlength="8"
            required
        /></label>
        <p v-if="passwordError" class="error">{{ passwordError }}</p>
        <button class="btn primary">保存并重新登录</button>
      </form>
    </ModalDialog>
    <RouterView :key="$route.path" />
  </div>
</template>
