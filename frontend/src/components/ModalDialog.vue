<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount } from "vue";
defineProps<{ labelledby: string }>();
const emit = defineEmits<{ close: [] }>();
const dialog = ref<HTMLDialogElement>();
function backdropClick(event: MouseEvent) {
  if (event.target !== dialog.value) return;
  const bounds = dialog.value!.getBoundingClientRect();
  if (
    event.clientX < bounds.left ||
    event.clientX > bounds.right ||
    event.clientY < bounds.top ||
    event.clientY > bounds.bottom
  )
    dialog.value!.close();
}
onMounted(() => dialog.value?.showModal());
onBeforeUnmount(() => dialog.value?.close());
</script>
<template>
  <dialog
    ref="dialog"
    class="modal-dialog"
    :aria-labelledby="labelledby"
    @close="emit('close')"
    @click="backdropClick"
  >
    <slot />
  </dialog>
</template>
