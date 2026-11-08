<script setup lang="ts">
defineProps<{ name: string }>();
const paths: Record<string, string> = {
  projects: "M3 7h7l2 2h9v10H3z M3 7V5h7l2 2",
  settings: "M4 6h16 M4 12h16 M4 18h16 M8 3v6 M16 9v6 M10 15v6",
  server: "M4 3h16v7H4z M4 14h16v7H4z M7 6h.01 M7 17h.01 M11 6h6 M11 17h6",
  text: "M6 3h8l4 4v14H6z M14 3v5h4 M9 12h6 M9 16h6",
  image: "M3 3h18v18H3z M3 16l6-6 7 7 3-3 2 2 M16 7h.01",
  search: "M21 21l-5-5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
  plus: "M12 5v14 M5 12h14",
  arrow: "M4 12h16 M14 6l6 6-6 6",
  menu: "M4 6h16 M4 12h16 M4 18h16",
  close: "M6 6l12 12 M6 18L18 6",
  check: "M4 12l5 5L20 6",
};
</script>
<template>
  <svg
    class="app-icon"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.7"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path :d="paths[name] || paths.projects" />
  </svg>
</template>
