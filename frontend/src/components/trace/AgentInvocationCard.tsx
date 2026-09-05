import { agentRoleLabel, type AgentInvocationView } from "./model";

const STATUS_LABELS: Record<string, string> = {
  succeeded: "成功",
  completed: "完成",
  degraded: "已降级",
  failed: "失败",
  running: "处理中",
  started: "处理中",
};

export function AgentInvocationCard({
  invocation,
  index,
}: {
  invocation: AgentInvocationView;
  index: number;
}) {
  return (
    <details
      className={`invocation-card invocation-${statusTone(invocation.status)}`}
      open={index === 1 || invocation.repaired || invocation.status !== "succeeded"}
    >
      <summary>
        <span className="invocation-index">{index}</span>
        <span className="invocation-title">
          <small>{invocation.agent_role}</small>
          <strong>{agentRoleLabel(invocation.agent_role)}</strong>
          <code>{invocation.agent_version}</code>
        </span>
        <span className="invocation-flags">
          {invocation.repaired && <span className="repair-badge">第 {invocation.attempt} 次 · 修复</span>}
          <StatusPill value={invocation.status} />
          <span>{formatDuration(invocation.duration_ms)}</span>
        </span>
      </summary>
      <div className="invocation-body">
        <div className="invocation-explanation">
          <div>
            <span>为什么调用</span>
            <p>{startReason(invocation)}</p>
          </div>
          <dl>
            <div><dt>模型调用</dt><dd>{invocation.model_call_count}</dd></div>
            <div><dt>工具调用</dt><dd>{invocation.tool_call_count}</dd></div>
            <div><dt>Token</dt><dd>{invocation.total_token_count}</dd></div>
            <div><dt>输入 Artifact</dt><dd>{invocation.input_artifact_ids.length}</dd></div>
          </dl>
        </div>
        {invocation.failure_code && (
          <div className="invocation-error">
            <strong>{invocation.failure_code}</strong>
            {invocation.failure_reason && <span>{invocation.failure_reason}</span>}
          </div>
        )}
        <div className="invocation-events">
          {invocation.events.map((event) => (
            <article key={`${event.event_type}-${event.id}`}>
              <header>
                <div><span>{event.presentation.stage}</span><strong>{event.presentation.title}</strong></div>
                <small>{formatDuration(event.duration_ms)}</small>
              </header>
              <p>{event.presentation.summary}</p>
              {event.presentation.facts.length > 0 && (
                <dl>
                  {event.presentation.facts.map((fact, factIndex) => (
                    <div key={`${fact.label}-${factIndex}`}><dt>{fact.label}</dt><dd>{fact.value}</dd></div>
                  ))}
                </dl>
              )}
              <details className="invocation-technical">
                <summary>技术详情</summary>
                <pre>{JSON.stringify({ operation: event.operation, details: event.details }, null, 2)}</pre>
              </details>
            </article>
          ))}
          {invocation.events.length === 0 && (
            <div className="invocation-empty">该 Invocation 没有更多可展示事件。</div>
          )}
        </div>
        <footer>
          <span>Invocation · <code>{invocation.invocation_id}</code></span>
          <span>上游 Invocation · <code>{invocation.parent_invocation_id ?? "Coordinator"}</code></span>
          <span>输出 Artifact · <code>{invocation.output_artifact_id ?? "—"}</code></span>
        </footer>
      </div>
    </details>
  );
}

function startReason(invocation: AgentInvocationView): string {
  if (invocation.reason_summary) return invocation.reason_summary;
  const start = invocation.events.find((event) => event.operation === "invocation_started");
  if (start && typeof start.details === "object" && start.details !== null) {
    const reason = (start.details as Record<string, unknown>).reason_summary;
    if (typeof reason === "string" && reason) return reason;
  }
  return "Coordinator 根据工作流状态和已验证 Artifact 调用了这个节点。";
}

function StatusPill({ value }: { value: string }) {
  return <span className={`invocation-status status-${statusTone(value)}`}>{STATUS_LABELS[value] ?? value}</span>;
}

function statusTone(value: string): string {
  if (["succeeded", "completed"].includes(value)) return "good";
  if (["failed", "degraded"].includes(value)) return "bad";
  return "warn";
}

function formatDuration(value: number | null): string {
  if (value == null) return "—";
  if (value < 1000) return `${value} ms`;
  return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)} s`;
}
