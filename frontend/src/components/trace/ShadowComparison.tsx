import type { ShadowComparisonView } from "./model";

export function ShadowComparison({ comparison }: { comparison: ShadowComparisonView }) {
  const hasNoBusinessWrites = ["none", "no_business_writes"].includes(comparison.business_writes);
  return (
    <section className="shadow-comparison">
      <header>
        <div>
          <span className="shadow-kicker">SHADOW EVALUATION</span>
          <h2>线上回复与新工作流候选对比</h2>
          <p>候选结果只用于评估，没有进入用户投递或业务写入链路。</p>
        </div>
        <div className="shadow-safety-flags">
          <span>{comparison.delivery_status === "not_sent" ? "未发送" : comparison.delivery_status}</span>
          <span>{hasNoBusinessWrites ? "无业务写入" : `业务写入：${comparison.business_writes}`}</span>
        </div>
      </header>
      <div className="shadow-panels">
        <ResponsePanel
          title="当前线上回复"
          subtitle="Legacy · 实际投递链路"
          content={comparison.legacy.content}
          status={comparison.legacy.status}
          tone="legacy"
        />
        <ResponsePanel
          title="新工作流候选"
          subtitle="Multi-Agent · Shadow only"
          content={comparison.candidate.content}
          status={comparison.delivery_status}
          tone="candidate"
        />
      </div>
      <footer>
        <span>候选 Artifact · <code>{comparison.candidate.artifact_id ?? "—"}</code></span>
        <span>业务写入 · <strong>{hasNoBusinessWrites ? "无" : comparison.business_writes}</strong></span>
      </footer>
    </section>
  );
}

function ResponsePanel({
  title,
  subtitle,
  content,
  status,
  tone,
}: {
  title: string;
  subtitle: string;
  content: string | null;
  status: string | null;
  tone: "legacy" | "candidate";
}) {
  return (
    <article className={`shadow-panel shadow-panel-${tone}`}>
      <header>
        <div><small>{subtitle}</small><h3>{title}</h3></div>
        <span>{statusLabel(status)}</span>
      </header>
      <p>{content || "本次 Trace 没有保留可展示的候选正文；可通过 Artifact ID 核对执行结果。"}</p>
    </article>
  );
}

function statusLabel(value: string | null): string {
  if (value === "not_sent") return "未发送";
  if (value === "accepted") return "已送达";
  if (value === "generated") return "已生成";
  return value ?? "状态未知";
}
