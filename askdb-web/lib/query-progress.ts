export type QueryProgressStatus = "running" | "completed" | "failed";

export type QueryProgressStep = {
  stepId: string;
  label: string;
  status: QueryProgressStatus;
};

const USER_FACING_LABELS = new Set([
  "准备查询",
  "理解问题",
  "分析查询需求",
  "查询数据",
  "生成图表",
  "整理结果",
]);

export function readQueryProgressStep(
  value: Record<string, unknown>,
): QueryProgressStep | undefined {
  if (
    typeof value.step_id !== "string" ||
    !value.step_id ||
    value.step_id.length > 128 ||
    typeof value.label !== "string" ||
    !USER_FACING_LABELS.has(value.label) ||
    (value.status !== "running" && value.status !== "completed" && value.status !== "failed")
  ) {
    return undefined;
  }
  return { stepId: value.step_id, label: value.label, status: value.status };
}
