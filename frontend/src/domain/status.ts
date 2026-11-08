export const workLabels: Record<string, string> = {
  unstarted: "未开始",
  draft: "草稿",
  submitted: "待审核",
  accepted: "已审核",
  returned: "已退回",
};
export const jobLabels: Record<string, string> = {
  queued: "排队中",
  running: "处理中",
  cancel_requested: "正在取消",
  cancelled: "已取消",
  succeeded: "已完成",
  partially_failed: "部分失败",
  failed: "失败",
};
export const jobTypes: Record<string, string> = {
  agent: "Agent 执行阶段",
  import: "数据导入",
  prediction: "模型预标注",
  export: "审核结果导出",
  training: "模型训练",
  evaluation: "模型评估",
  deploy: "模型部署",
  stop_deployment: "停止模型",
};
export function statusTone(state: string): string {
  if (["accepted", "succeeded", "ready"].includes(state)) return "success";
  if (["failed", "returned", "partially_failed"].includes(state))
    return "danger";
  if (["submitted", "running", "queued", "cancel_requested"].includes(state))
    return "pending";
  return "neutral";
}
