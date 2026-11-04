import type { CallDetail, ProcessNode, Tone } from "./types";

export function needsRefresh(data: CallDetail | undefined) {
  return (
    !data ||
    ["running", "pending"].includes(data.trace.generation_status) ||
    ["planned", "sending", "running"].includes(data.trace.delivery_status) ||
    data.process.background_jobs.some((job) =>
      ["queued", "running"].includes(job.status),
    )
  );
}

export const labels: Record<string, string> = {
  completed: "处理完成",
  succeeded: "成功",
  failed: "失败",
  running: "处理中",
  pending: "等待中",
  planned: "等待返回",
  recovered: "修复后完成",
  partial: "部分完成",
  waiting: "等待用户回答",
  rejected: "未通过审查",
  warning: "表达建议",
  degraded: "已降级",
  unknown: "未记录／未知",
  none: "未执行",
  accepted: "已返回／渠道已接受",
  cancelled: "已取消",
  not_reported: "未上报",
  queued: "已入队",
  started: "处理中",
  not_applied: "未执行写入",
  skipped: "未发送",
  deferred_external_session: "人工会话中",
};
export function tone(status: string): Tone {
  return ["succeeded", "completed", "accepted"].includes(status)
    ? "good"
    : ["failed", "degraded", "partial", "cancelled"].includes(status)
      ? "bad"
      : [
            "recovered",
            "waiting",
            "running",
            "pending",
            "rejected",
            "warning",
            "started",
          ].includes(status)
        ? "warn"
        : "";
}
export function date(value: string | null | undefined) {
  if (!value || !Number.isFinite(Date.parse(value))) return "未记录";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}
export function elapsed(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return "未记录";
  return value < 1000
    ? `${Math.round(value)}ms`
    : `${(value / 1000).toFixed(1)}s`;
}
export function object(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}
export function evidenceRefs(value: unknown): string[] {
  const found = new Set<string>();
  const visit = (v: unknown, depth: number) => {
    if (depth > 12 || !v || typeof v !== "object") return;
    for (const [key, child] of Object.entries(v)) {
      if (
        [
          "source_refs",
          "evidence_refs",
          "allowed_source_refs",
          "parent_artifact_ids",
          "input_artifact_ids",
        ].includes(key) &&
        Array.isArray(child)
      ) {
        for (const ref of child) if (typeof ref === "string") found.add(ref);
      } else visit(child, depth + 1);
    }
  };
  visit(value, 0);
  return [...found];
}
export function roots(nodes: ProcessNode[]) {
  const ids = new Set(nodes.map((n) => n.id));
  return nodes.filter((n) => !n.parent_id || !ids.has(n.parent_id));
}
export function problemNode(nodes: ProcessNode[]) {
  return (
    nodes.find(
      (n) => n.kind === "model" && ["failed", "cancelled"].includes(n.status),
    ) ||
    nodes.find(
      (n) =>
        ["failed", "rejected", "degraded", "cancelled"].includes(n.status) ||
        n.issues.length,
    )
  );
}
