const STATUS_LABELS: Record<string, string> = {
  accepted: "已送达",
  completed: "完成",
  succeeded: "成功",
  running: "处理中",
  started: "处理中",
  sending: "发送中",
  planned: "已计划",
  pending: "等待中",
  pending_review: "待审核",
  waiting: "等待确认",
  degraded: "已降级",
  failed: "失败",
  unknown: "结果未知",
  skipped: "已跳过",
  deferred_external_session: "人工会话中",
};

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

export function StatusBadge({ value }: { value: string }) {
  const tone = ["accepted", "completed", "succeeded"].includes(value)
    ? "good"
    : ["failed", "unknown", "degraded"].includes(value)
      ? "bad"
      : ["waiting", "pending_review", "sending", "running"].includes(value)
        ? "warn"
        : "neutral";
  return (
    <span className={`status status-${tone}`}>
      {STATUS_LABELS[value] ?? value}
    </span>
  );
}

export function Loading({ label = "正在读取数据" }: { label?: string }) {
  return (
    <div className="state-card">
      <span className="spinner" />
      {label}
    </div>
  );
}

export function Failure({ error }: { error: unknown }) {
  return (
    <div className="state-card state-error">
      {error instanceof Error ? error.message : "读取失败"}
    </div>
  );
}
