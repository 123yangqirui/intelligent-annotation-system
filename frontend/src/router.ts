import { createRouter, createWebHistory } from "vue-router";
import { user, restoreSession } from "./api/http";
import LoginView from "./views/LoginView.vue";
import ProjectsView from "./views/ProjectsView.vue";
import ProjectView from "./views/ProjectView.vue";
import AnnotationView from "./views/AnnotationView.vue";
import AdminView from "./views/AdminView.vue";
import HealthView from "./HealthView.vue";

export const router = createRouter({
  history: createWebHistory(),
  scrollBehavior(to, from, savedPosition) {
    if (savedPosition) return savedPosition;
    if (to.path !== from.path) return { top: 0 };
  },
  routes: [
    { path: "/login", component: LoginView },
    { path: "/health", component: HealthView },
    { path: "/", component: ProjectsView },
    { path: "/projects/:id", component: ProjectView },
    { path: "/annotate/:id", component: AnnotationView },
    { path: "/admin", component: AdminView },
    { path: "/:pathMatch(.*)*", redirect: "/" },
  ],
});

router.beforeEach(async (to) => {
  if (to.path === "/health" || to.path === "/login") return;
  if (!user.value && !(await restoreSession())) return "/login";
  if (to.path === "/admin" && !user.value?.is_admin) return "/";
});
