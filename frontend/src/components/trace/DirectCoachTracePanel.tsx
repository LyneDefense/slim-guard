import type { WorkflowTraceView } from "./model";

export function DirectCoachTracePanel({ workflow }: { workflow: WorkflowTraceView }) {
  const resolution = [...workflow.artifacts].reverse().find(a => a.artifact_type === "style_resolution")?.payload;
  const style = record(resolution);
  const replies = workflow.artifacts.filter(a => a.artifact_type === "coach_reply");
  const reviews = workflow.artifacts.filter(a => a.artifact_type === "coach_grounding_review");
  const final = record([...workflow.artifacts].reverse().find(a => a.artifact_type === "outgoing_messages")?.payload);
  return <section className="style-trace-panel">
    <div className="section-heading"><div><h2>医生直接回复</h2>
      <p>原始上下文和真实回执 → 已发布风格直接生成 → 证据审查。没有中性原稿或二次风格改写。</p>
    </div><span>直接生成</span></div>
    <div className="style-profile-strip">
      <div><span>风格版本</span><code>{String(style.profile_version ?? "未记录")}</code></div>
      <div><span>加载来源</span><strong>{String(style.source ?? "未记录")}</strong></div>
      <div><span>表达示例</span><strong>{Array.isArray(style.example_ids) ? style.example_ids.length : 0} 条</strong></div>
      <div><span>最终状态</span><strong>{String(final.status ?? "执行中")}</strong></div>
      {typeof final.adopted_attempt === "number" && <div><span>采用候选</span><strong>第 {final.adopted_attempt} 次</strong></div>}
    </div>
    {Boolean(style.fallback_reason) && <p role="status">风格资产回退：{String(style.fallback_reason)}</p>}
    {final.style_advisory === true && <p role="status">仍有表达建议；已保留通过事实与职责审查的医生回答，不是生成失败。</p>}
    {replies.map((artifact, index) => {
      const payload = record(artifact.payload);
      const check = record(reviews.find(a => a.parent_artifact_ids?.includes(artifact.artifact_id))?.payload);
      return <article key={artifact.artifact_id} className="style-block-summary">
        <h3>候选 {index + 1} · {payload.failure_code ? "生成失败" : "已生成"}
          {typeof final.adopted_attempt === "number" && final.adopted_attempt === payload.attempt ? " · 最终采用" : ""}</h3>
        <p>{String(record(payload.output).text ?? "无可用候选")}</p>
        <details><summary>结构诊断及证据审查</summary>
          <pre>{JSON.stringify({ validation_errors: payload.validation_errors, review: check.output,
            review_failure: check.failure_code }, null, 2)}</pre>
        </details>
      </article>;
    })}
    {Boolean(final.failure_code) && <p role="alert">医生回复未放行：{String(final.failure_code)}。系统操作回执独立保留。</p>}
  </section>;
}

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
}
