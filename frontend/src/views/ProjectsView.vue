<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { api, post } from "../api/http";
import { RouterLink } from "vue-router";
import AppIcon from "../components/AppIcon.vue";
import ModalDialog from "../components/ModalDialog.vue";
const projects = ref<any[]>([]);
const name = ref("");
const description = ref("");
const taskType = ref("text_classification");
const error = ref("");
const showCreate = ref(false);
const busy = ref(false);
const search = ref("");
const filterType = ref("");
const filterStatus = ref("active");
const visibleProjects = computed(() =>
  projects.value.filter(
    (project) =>
      (!filterType.value || project.task_type === filterType.value) &&
      (!filterStatus.value || project.status === filterStatus.value) &&
      `${project.name} ${project.description}`
        .toLowerCase()
        .includes(search.value.trim().toLowerCase()),
  ),
);
const activeProjects = computed(() =>
  projects.value.filter((project) => project.status === "active"),
);
async function load() {
  try {
    projects.value = await api("/projects");
  } catch (e) {
    error.value = (e as Error).message;
  }
}
async function create() {
  busy.value = true;
  error.value = "";
  try {
    await post("/projects", {
      name: name.value,
      description: description.value,
      task_type: taskType.value,
    });
    name.value = "";
    description.value = "";
    showCreate.value = false;
    await load();
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
onMounted(load);
</script>
<template>
  <main class="workspace">
    <div class="page-title">
      <div>
        <p class="kicker">工作空间</p>
        <h1>我的项目</h1>
        <p class="muted">按任务组织数据、标注规范与审核成果。</p>
      </div>
      <button class="btn primary" @click="showCreate = !showCreate">
        <AppIcon name="plus" />新建项目
      </button>
    </div>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <ModalDialog
      v-if="showCreate"
      labelledby="project-create-title"
      @close="showCreate = false"
    >
      <form class="panel modal-panel" @submit.prevent="create">
        <div class="row-between">
          <h2 id="project-create-title">创建标注项目</h2>
          <button
            type="button"
            class="icon-button"
            aria-label="关闭创建项目"
            @click="showCreate = false"
          >
            <AppIcon name="close" />
          </button>
        </div>
        <div class="form-grid">
          <label
            >项目名称<input
              v-model="name"
              required
              maxlength="100"
              placeholder="例如：客服意图分类" /></label
          ><label
            >任务类型<select v-model="taskType">
              <option value="text_classification">文本分类</option>
              <option value="image_detection">图像目标检测</option>
            </select></label
          >
        </div>
        <label
          >项目说明<textarea
            v-model="description"
            maxlength="1000"
            placeholder="这个项目需要标注什么？"
          ></textarea></label
        ><button class="btn primary" :disabled="busy">创建项目</button>
      </form>
    </ModalDialog>
    <div class="overview-grid">
      <article>
        <span class="overview-icon"><AppIcon name="projects" /></span>
        <div>
          <span>进行中的项目</span><strong>{{ activeProjects.length }}</strong>
        </div>
      </article>
      <article>
        <span class="overview-icon text-icon"><AppIcon name="text" /></span>
        <div>
          <span>文本分类</span
          ><strong>{{
            activeProjects.filter(
              (project) => project.task_type === "text_classification",
            ).length
          }}</strong>
        </div>
      </article>
      <article>
        <span class="overview-icon image-icon"><AppIcon name="image" /></span>
        <div>
          <span>图像目标检测</span
          ><strong>{{
            activeProjects.filter(
              (project) => project.task_type === "image_detection",
            ).length
          }}</strong>
        </div>
      </article>
    </div>
    <div class="project-list-toolbar">
      <h2>
        项目列表 <span class="count-pill">{{ visibleProjects.length }}</span>
      </h2>
      <div class="inline-controls">
        <label class="search-field"
          ><AppIcon name="search" /><input
            v-model="search"
            aria-label="搜索项目"
            placeholder="搜索项目名称或说明"
            type="search"
        /></label>
        <select v-model="filterType" aria-label="筛选任务类型">
          <option value="">全部任务类型</option>
          <option value="text_classification">文本分类</option>
          <option value="image_detection">图像目标检测</option>
        </select>
        <select v-model="filterStatus" aria-label="筛选项目状态">
          <option value="active">进行中</option>
          <option value="archived">已归档</option>
          <option value="">全部状态</option>
        </select>
      </div>
    </div>
    <div v-if="!projects.length" class="panel empty">
      <span class="empty-symbol">▤</span>
      <h2>创建你的第一个项目</h2>
      <p class="muted">选择文本分类或图像检测，然后定义标签并导入数据。</p>
    </div>
    <div v-else-if="!visibleProjects.length" class="panel empty">
      <AppIcon name="search" />
      <h2>没有匹配的项目</h2>
      <p class="muted">试试其他关键词，或调整任务类型和状态。</p>
    </div>
    <div class="project-grid">
      <RouterLink
        v-for="project in visibleProjects"
        :key="project.id"
        :to="`/projects/${project.id}`"
        class="panel project-card"
        ><div class="row-between">
          <span class="type-badge"
            ><AppIcon
              :name="
                project.task_type === 'text_classification' ? 'text' : 'image'
              "
            />{{
              project.task_type === "text_classification"
                ? "文 · 文本分类"
                : "图 · 目标检测"
            }}</span
          ><span class="badge">{{
            project.status === "active" ? "进行中" : "已归档"
          }}</span>
        </div>
        <h2>{{ project.name }}</h2>
        <p class="muted">{{ project.description || "尚未填写项目说明" }}</p>
        <footer>
          <span>{{
            new Date(project.created_at).toLocaleDateString("zh-CN")
          }}</span
          ><span class="card-link"
            >进入项目 <AppIcon name="arrow"
          /></span></footer
      ></RouterLink>
    </div>
  </main>
</template>
