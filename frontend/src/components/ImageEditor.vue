<script setup lang="ts">
import { ref, watch, onMounted, onUnmounted } from "vue";
import Konva from "konva";
import { clampBox } from "../domain/geometry";
export interface Box {
  object_id: string;
  label_id: string;
  xyxy: number[];
}
const props = defineProps<{
  src: string;
  modelValue: Box[];
  labels: { id: string; name: string; color: string }[];
  labelId: string;
  disabled?: boolean;
}>();
const emit = defineEmits<{ "update:modelValue": [Box[]] }>();
const container = ref<HTMLDivElement>();
const mode = ref("draw");
const selected = ref("");
let stage: Konva.Stage;
let layer: Konva.Layer;
let boxesLayer: Konva.Layer;
let transformer: Konva.Transformer;
let width = 1;
let height = 1;
let start: { x: number; y: number } | null = null;
let temporary: Konva.Rect | null = null;
let resize: ResizeObserver;
let imageRequest = 0;
function point() {
  const pos = stage.getPointerPosition();
  if (!pos) return { x: 0, y: 0 };
  const p = stage.getAbsoluteTransform().copy().invert().point(pos);
  return {
    x: Math.max(0, Math.min(width, p.x)),
    y: Math.max(0, Math.min(height, p.y)),
  };
}
function update(box: Box) {
  if (props.disabled) return;
  emit(
    "update:modelValue",
    props.modelValue.map((x) => (x.object_id === box.object_id ? box : x)),
  );
}
function redraw() {
  if (!boxesLayer) return;
  boxesLayer.destroyChildren();
  transformer = new Konva.Transformer({
    rotateEnabled: false,
    flipEnabled: false,
    boundBoxFunc: (oldBox, newBox) =>
      newBox.width < 1 || newBox.height < 1 ? oldBox : newBox,
  });
  boxesLayer.add(transformer);
  for (const box of props.modelValue) {
    const [x1 = 0, y1 = 0, x2 = 1, y2 = 1] = box.xyxy;
    const label = props.labels.find((x) => x.id === box.label_id);
    const rect = new Konva.Rect({
      id: box.object_id,
      x: x1,
      y: y1,
      width: x2 - x1,
      height: y2 - y1,
      stroke: label?.color || "#4268e8",
      strokeWidth: 2 / stage.scaleX(),
      draggable: mode.value === "select" && !props.disabled,
      fill: "#4268e808",
    });
    boxesLayer.add(rect);
    const title = new Konva.Text({
      x: x1,
      y: Math.max(0, y1 - 17 / stage.scaleX()),
      text: label?.name || "未分类",
      fontSize: 12 / stage.scaleX(),
      fill: label?.color || "#4268e8",
      listening: false,
    });
    boxesLayer.add(title);
    rect.on("click tap", (e) => {
      if (mode.value === "select" && !props.disabled) {
        e.cancelBubble = true;
        selected.value = box.object_id;
        transformer.nodes([rect]);
        boxesLayer.batchDraw();
      }
    });
    rect.on("dragend transformend", () => {
      const coords = clampBox(
        rect.x(),
        rect.y(),
        rect.width() * rect.scaleX(),
        rect.height() * rect.scaleY(),
        width,
        height,
      );
      rect.scale({ x: 1, y: 1 });
      update({ ...box, xyxy: coords });
    });
    if (selected.value === box.object_id) transformer.nodes([rect]);
  }
  boxesLayer.batchDraw();
}
function fit() {
  if (!stage || !container.value) return;
  stage.width(container.value.clientWidth);
  const scale = Math.min(stage.width() / width, stage.height() / height) * 0.94;
  stage.scale({ x: scale, y: scale });
  stage.position({
    x: (stage.width() - width * scale) / 2,
    y: (stage.height() - height * scale) / 2,
  });
  redraw();
}
function zoom(factor: number) {
  const center = { x: stage.width() / 2, y: stage.height() / 2 };
  const p = stage.getAbsoluteTransform().copy().invert().point(center);
  const scale = Math.max(0.05, Math.min(10, stage.scaleX() * factor));
  stage.scale({ x: scale, y: scale });
  stage.position({ x: center.x - p.x * scale, y: center.y - p.y * scale });
  redraw();
}
function remove() {
  if (props.disabled || !selected.value) return;
  emit(
    "update:modelValue",
    props.modelValue.filter((x) => x.object_id !== selected.value),
  );
  selected.value = "";
}
async function loadImage() {
  if (!stage || !props.src) return;
  const request = ++imageRequest;
  const image = new Image();
  image.src = props.src;
  await image.decode();
  if (request !== imageRequest) return;
  width = image.naturalWidth;
  height = image.naturalHeight;
  layer.destroyChildren();
  layer.add(new Konva.Image({ image, listening: false }));
  fit();
}
watch(() => props.src, loadImage);
watch(() => [props.modelValue, props.disabled], redraw, { deep: true });
watch(mode, () => {
  if (stage) {
    stage.draggable(mode.value === "pan");
    selected.value = "";
    redraw();
  }
});
onMounted(() => {
  stage = new Konva.Stage({
    container: container.value!,
    width: container.value!.clientWidth,
    height: 520,
  });
  layer = new Konva.Layer();
  boxesLayer = new Konva.Layer();
  stage.add(layer, boxesLayer);
  stage.on("mousedown touchstart", (e) => {
    if (props.disabled || mode.value !== "draw" || !props.labelId) return;
    // Existing boxes remain editable and a new overlapping box can start on them.
    start = point();
    temporary = new Konva.Rect({
      x: start.x,
      y: start.y,
      width: 0,
      height: 0,
      stroke: "#4268e8",
      strokeWidth: 2 / stage.scaleX(),
      listening: false,
    });
    boxesLayer.add(temporary);
  });
  stage.on("mousemove touchmove", () => {
    if (!start || !temporary) return;
    const end = point();
    temporary.setAttrs({
      x: Math.min(start.x, end.x),
      y: Math.min(start.y, end.y),
      width: Math.abs(end.x - start.x),
      height: Math.abs(end.y - start.y),
    });
    boxesLayer.batchDraw();
  });
  stage.on("mouseup touchend", () => {
    if (!temporary) return;
    const rect = temporary;
    temporary = null;
    start = null;
    if (rect.width() >= 1 && rect.height() >= 1)
      emit("update:modelValue", [
        ...props.modelValue,
        {
          object_id: crypto.randomUUID(),
          label_id: props.labelId,
          xyxy: [
            rect.x(),
            rect.y(),
            rect.x() + rect.width(),
            rect.y() + rect.height(),
          ],
        },
      ]);
    rect.destroy();
  });
  stage.on("wheel", (e) => {
    e.evt.preventDefault();
    zoom(e.evt.deltaY > 0 ? 0.9 : 1.1);
  });
  resize = new ResizeObserver(() => fit());
  resize.observe(container.value!);
  loadImage();
});
onUnmounted(() => {
  imageRequest++;
  resize?.disconnect();
  stage?.destroy();
});
</script>
<template>
  <div class="image-editor">
    <div class="editor-tools">
      <button :class="{ active: mode === 'draw' }" @click="mode = 'draw'">
        画框</button
      ><button :class="{ active: mode === 'select' }" @click="mode = 'select'">
        选择 / 调整</button
      ><button :class="{ active: mode === 'pan' }" @click="mode = 'pan'">
        平移</button
      ><span></span><button @click="zoom(0.8)">−</button
      ><button @click="fit">适应画布</button
      ><button @click="zoom(1.25)">＋</button
      ><button :disabled="!selected" @click="remove">删除选中框</button>
    </div>
    <div
      class="canvas-container"
      ref="container"
      tabindex="0"
      @keydown.delete.prevent="remove"
    ></div>
  </div>
</template>
