import { useState } from "react";
import type { CallDetail, ProcessNode } from "./types";
import {
  date,
  elapsed,
  evidenceRefs,
  labels,
  object,
  roots,
  tone,
} from "./presentation";

export function Badge({ value, label }: { value: string; label?: string }) {
  return (
    <span className={`call-badge ${tone(value)}`}>
      {label || labels[value] || value}
    </span>
  );
}
export function Json({ value }: { value: unknown }) {
  return value == null ? (
    <p className="call-muted">未记录；不从其他步骤推测或补写。</p>
  ) : (
    <pre className="call-json">
      {typeof value === "string" ? value : JSON.stringify(value, null, 2)}
    </pre>
  );
}

function ResultSummary({ value }: { value: unknown }) {
  const packet = object(value);
  const body = object(packet.output ?? value);
  const text =
    typeof value === "string"
      ? value
      : (body.text ?? body.content ?? body.description);
  if (typeof text === "string") {
    return (
      <p className="call-note">
        {text.slice(0, 800)}
        {text.length > 800 && "…（完整内容见输入输出）"}
      </p>
    );
  }
  const dimensions = [
    ["fidelity_passed", "事实依据"],
    ["role_passed", "角色职责"],
    ["expression_passed", "表达风格"],
  ];
  const checks = dimensions.filter(([key]) => typeof body[key] === "boolean");
  if (checks.length) {
    return (
      <div>
        {checks.map(([key, label]) => (
          <p className="call-field" key={key}>
            {label} <Badge value={body[key] ? "succeeded" : "rejected"} />
          </p>
        ))}
      </div>
    );
  }
  if (value == null) return <Json value={null} />;
  return (
    <details>
      <summary>查看已记录的结构化结果</summary>
      <Json value={value} />
    </details>
  );
}
export function CallState({
  error,
  loading,
  retry,
}: {
  error?: unknown;
  loading?: boolean;
  retry?: () => void;
}) {
  return (
    <div className="call-empty" role={error ? "alert" : "status"}>
      {loading
        ? "正在读取调用过程…"
        : error instanceof Error
          ? error.message
          : "暂时无法读取调用过程"}
      {retry && (
        <button className="call-button" onClick={retry}>
          重新加载
        </button>
      )}
    </div>
  );
}
export function Flow({
  nodes,
  selected,
  expanded,
  onSelect,
}: {
  nodes: ProcessNode[];
  selected: string;
  expanded: boolean;
  onSelect: (id: string) => void;
}) {
  function row(node: ProcessNode, path: Set<string>) {
    if (path.has(node.id)) return null;
    const children = nodes.filter((n) => n.parent_id === node.id);
    const next = new Set([...path, node.id]);
    return (
      <div className="call-node" key={node.id}>
        <button
          className="call-node-button"
          aria-pressed={selected === node.id}
          onClick={() => onSelect(node.id)}
        >
          <span className={`call-dot ${tone(node.status)}`} aria-hidden="true">
            {tone(node.status) === "bad"
              ? "!"
              : tone(node.status) === "warn"
                ? "↻"
                : tone(node.status) === "good"
                  ? "✓"
                  : "·"}
          </span>
          <span className="call-node-copy">
            <span className="call-node-title">
              {node.title}
              {node.kind === "model" && (
                <span className="call-badge">模型</span>
              )}
              {node.kind === "tool" && <span className="call-badge">工具</span>}
            </span>
            <span className="call-node-summary">
              {node.summary || labels[node.status] || node.status}
            </span>
          </span>
          <span className="call-duration">{elapsed(node.duration_ms)}</span>
        </button>
        {expanded && children.length > 0 && (
          <div className="call-children">
            {children.map((child) => row(child, next))}
          </div>
        )}
      </div>
    );
  }
  return (
    <div className="call-flow">
      {roots(nodes).map((n) => row(n, new Set()))}
    </div>
  );
}
export function Waterfall({
  nodes,
  start,
  end,
  selected,
  onSelect,
}: {
  nodes: ProcessNode[];
  start: string;
  end: string | null;
  selected: string;
  onSelect: (id: string) => void;
}) {
  const origin = Date.parse(start);
  const timed = nodes.filter(
    (n) =>
      n.duration_ms != null &&
      n.started_at &&
      n.completed_at &&
      Number.isFinite(Date.parse(n.started_at)),
  );
  const last = Math.max(
    Number.isFinite(Date.parse(end || "")) ? Date.parse(end!) : origin,
    ...timed.map((n) => Date.parse(n.completed_at!)),
  );
  const span = Math.max(1, last - origin);
  return (
    <div className="call-waterfall">
      <div className="call-time-axis">
        <span>主线 · 相对时间</span>
        <div>
          <span>0s</span>
          <span>{elapsed(span / 2)}</span>
          <span>{elapsed(span)}</span>
        </div>
      </div>
      {timed.map((n) => (
        <button
          className="call-time-row"
          key={n.id}
          aria-pressed={n.id === selected}
          onClick={() => onSelect(n.id)}
        >
          <span>
            {n.title}
            <small>{elapsed(n.duration_ms)}</small>
          </span>
          <span className="call-track">
            <span
              className={`call-bar ${tone(n.status)}`}
              style={{
                left: `${Math.max(0, ((Date.parse(n.started_at!) - origin) / span) * 100)}%`,
                width: `${Math.min(100, ((n.duration_ms || 0) / span) * 100)}%`,
              }}
            />
          </span>
        </button>
      ))}
      <p className="call-muted call-small">
        父阶段包含子调用，不能相加；未记录起止时间的步骤不画耗时条。
      </p>
    </div>
  );
}
export function Inspector({
  data,
  selected,
  onSelect,
}: {
  data: CallDetail;
  selected: ProcessNode;
  onSelect: (id: string) => void;
}) {
  const [tab, setTab] = useState("overview");
  const refs = [
    ...new Set([
      ...selected.evidence_refs,
      ...evidenceRefs(selected.output),
      ...evidenceRefs(selected.input),
    ]),
  ];
  const raw = object(selected.raw);
  const output = object(selected.output);
  const errors = output.validation_errors;
  const field = (label: string, content: React.ReactNode) => (
    <div className="call-field">
      <span className="call-field-label">{label}</span>
      <div className="call-field-text">{content}</div>
    </div>
  );
  return (
    <aside className="call-panel call-inspector" aria-label="步骤详情">
      <div className="call-inspector-title">
        <h2>{selected.title}</h2>
        <small>
          {selected.id} · {selected.kind}
        </small>
      </div>
      <div
        className="call-inspector-tabs call-mode"
        role="group"
        aria-label="步骤详情标签"
      >
        {[
          ["overview", "概览"],
          ["io", "输入输出"],
          ["evidence", "依据"],
          ["raw", "原始数据"],
        ].map(([value, label]) => (
          <button
            key={value}
            aria-pressed={tab === value}
            onClick={() => setTab(value)}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="call-inspector-content">
        {tab === "overview" && (
          <>
            {field(
              "执行结果",
              <>
                <Badge value={selected.status} />
                <p>{selected.summary}</p>
              </>,
            )}
            {field(
              "时间范围",
              `${date(selected.started_at)} → ${date(selected.completed_at)} · ${elapsed(selected.duration_ms)}`,
            )}
            {raw.model != null &&
              field(
                "模型与用量",
                <>
                  {String(raw.model)}
                  <br />
                  Token：
                  {raw.total_tokens == null
                    ? "未记录，不按 0 计"
                    : String(raw.total_tokens)}
                </>,
              )}
            {raw.agent_version != null &&
              field("执行版本", String(raw.agent_version))}
            {raw.idempotency_key != null &&
              field(
                "防重与回执",
                <Json
                  value={{
                    idempotency_key: raw.idempotency_key,
                    operation: object(selected.output).operation,
                  }}
                />,
              )}
            {selected.issues.map((issue, i) => (
              <div key={i} className="call-note warn call-field">
                {field("问题代码", String(issue.code || "未记录"))}
                {field(
                  "问题片段",
                  String(
                    issue.excerpt || issue.original_excerpt || "未提供片段",
                  ),
                )}
                {field(
                  "修复要求",
                  String(
                    issue.repair_requirement || issue.explanation || "未记录",
                  ),
                )}
              </div>
            ))}
            {Array.isArray(errors) &&
              errors.length > 0 &&
              field("结构校验与修复反馈", <Json value={errors} />)}
            {field("实际输出", <ResultSummary value={selected.output} />)}
          </>
        )}
        {tab === "io" && (
          <>
            {field("输入快照", <Json value={selected.input} />)}
            {field("实际返回", <Json value={selected.output} />)}
            <p className="call-muted call-small">
              仅展示已保存、授权访问的输入输出。缺失或已过保留期的快照不补造；不展示隐藏思维链。
            </p>
          </>
        )}
        {tab === "evidence" && (
          <>
            {refs.length === 0 && (
              <p className="call-muted">此步骤没有记录可定位的引用。</p>
            )}
            {refs.map((ref) => {
              const source = data.process.sources.find((s) => s.id === ref);
              const target = data.process.nodes.find(
                (n) => n.id === ref || n.id === source?.node_id,
              );
              const artifact = data.process.artifacts.find(
                (a) => a.artifact_id === ref,
              );
              return (
                <div className="call-field" key={ref}>
                  {target ? (
                    <button
                      className="call-evidence"
                      onClick={() => onSelect(target.id)}
                    >
                      {ref} ↗<small>{target.title}</small>
                    </button>
                  ) : (
                    <span className="call-muted">{ref} · 未记录步骤关联</span>
                  )}
                  {artifact && (
                    <details>
                      <summary>查看来源快照 · {artifact.artifact_type}</summary>
                      <Json value={artifact.payload} />
                    </details>
                  )}
                </div>
              );
            })}
          </>
        )}
        {tab === "raw" && (
          <Json
            value={{
              ...object(selected.raw),
              node_id: selected.id,
              parent_id: selected.parent_id,
            }}
          />
        )}
      </div>
    </aside>
  );
}
export function FinalOutput({ data }: { data: CallDetail }) {
  return (
    <section className="call-panel call-final">
      <div className="call-panel-head">
        <h2>实际返回给 App／渠道的内容</h2>
        <span className="call-muted call-small">服务已返回 ≠ 用户已阅读</span>
      </div>
      <div className="call-preview">
        {data.process.messages.length === 0 &&
          (data.output?.content ? (
            <>
              <div className="call-preview-label">
                历史／单段输出 · 未记录角色分块
              </div>
              <div className="call-doctor-bubble">{data.output.content}</div>
            </>
          ) : (
            <p className="call-muted">尚无已保存的最终输出。</p>
          ))}
        {data.process.messages.map((message, i) => (
          <div key={message.id || i} className="call-output-message">
            <div className="call-preview-label">
              {message.participant === "coach"
                ? "医生教练 · 最终采用稿"
                : "系统提示／卡片"}
            </div>
            <div
              className={
                message.participant === "coach"
                  ? "call-doctor-bubble"
                  : "call-notice-preview"
              }
            >
              {message.text || message.notice_text}
              {message.card && <Json value={message.card} />}
              {message.requires_response && <Badge value="waiting" />}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
