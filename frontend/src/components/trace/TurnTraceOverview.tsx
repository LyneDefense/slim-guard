import type { TraceDetail } from "../../types";
import { agentRoleLabel, type WorkflowTraceView } from "./model";

export function TurnTraceOverview({
  data,
  workflow,
}: {
  data: TraceDetail;
  workflow: WorkflowTraceView;
}) {
  const userText = data.input.messages
    .map((message) => message.text ?? (message.redacted ? "[内容已脱敏]" : ""))
    .filter(Boolean)
    .join("\n");
  const finalText = data.output?.content ?? data.style_comparison?.final_text ?? null;
  const style = data.style_comparison;
  const adoptedRender = style?.renders.find(
    (render) => render.artifact_id === style.final_artifact_id,
  ) ?? null;
  const rag = ragOverview(workflow);

  return (
    <>
      <section className="turn-endpoints">
        <TraceTextCard
          eyebrow="TURN START"
          title="用户这一轮说了什么"
          text={userText || (data.input.images.length > 0 ? "用户发送了图片" : "没有可显示的文字输入")}
          footer={data.input.images.length > 0 ? `${data.input.images.length} 张图片随本轮输入` : null}
        />
        <div className="turn-endpoint-arrow" aria-hidden="true">→</div>
        <TraceTextCard
          eyebrow="TURN END"
          title="用户实际收到的回复"
          text={finalText ?? "尚未记录最终发送文案"}
          footer={`生成 ${statusLabel(data.trace.generation_status)} · 投递 ${statusLabel(data.trace.delivery_status)}`}
          tone="final"
        />
      </section>

      <section className="turn-route-card">
        <header>
          <div><span className="eyebrow">EXECUTION ROUTE</span><h2>这句话经过了哪些步骤</h2></div>
          <small>{workflow.invocations.length} 次 Agent 调用 · {data.tool_executions.length} 次业务工具执行</small>
        </header>
        <div className="turn-route">
          <RouteNode label="Turn Harness" detail="创建本轮、冻结边界" status={data.turn ? "succeeded" : "unknown"} />
          {workflow.invocations.map((invocation) => (
            <RouteNode
              key={invocation.invocation_id}
              label={agentRoleLabel(invocation.agent_role)}
              detail={invocationDetail(invocation.agent_role, invocation.attempt)}
              status={invocation.status}
            />
          ))}
          <RouteNode
            label="最终放行与发送"
            detail={data.output ? "已形成渠道输出" : "尚未形成渠道输出"}
            status={data.output ? data.output.status : "unknown"}
          />
        </div>
        {data.tool_executions.length > 0 && (
          <div className="turn-tool-strip">
            <span>本轮工具</span>
            {data.tool_executions.map((tool, index) => (
              <code key={`${String(tool.tool_call_id ?? tool.tool_name)}-${index}`}>
                {toolLabel(String(tool.tool_name ?? "unknown"))} · {statusLabel(String(tool.status ?? "unknown"))}
              </code>
            ))}
          </div>
        )}
      </section>

      {rag && <RagRuntimeOverview rag={rag} />}

      {style && style.neutral_text && adoptedRender && (
        <section className="style-before-after">
          <header>
            <div><span className="eyebrow">STYLE EFFECT</span><h2>医生风格具体改了什么</h2></div>
            <div className="style-version-badge">唯一线上 Profile · {style.profile_version ?? "版本未记录"}</div>
          </header>
          <div className="style-compare-grid">
            <TraceTextCard
              eyebrow="风格前"
              title="Core 的中性内容稿"
              text={style.neutral_text ?? "未记录中性稿"}
            />
            <div className="style-compare-arrow" aria-hidden="true">→</div>
            <TraceTextCard
              eyebrow="风格后"
              title="实际采用的表达"
              text={adoptedRender?.text ?? style.final_text ?? "未记录风格稿"}
              footer={adoptedRender ? `第 ${adoptedRender.attempt} 次渲染${adoptedRender.used_fallback ? " · 使用中性降级" : ""}` : null}
              tone="final"
            />
          </div>
          {style.renders.length > 1 && (
            <div className="style-attempts">
              {style.renders.map((render) => (
                <article key={render.artifact_id}>
                  <strong>{render.profile_version ?? style.profile_version ?? "同一 Profile"} · 第 {render.attempt} 次渲染</strong>
                  <p>{render.text}</p>
                  <small>{render.artifact_id === style.final_artifact_id ? "审查后采用" : "未采用或进入返工"}</small>
                </article>
              ))}
            </div>
          )}
        </section>
      )}

      <HarnessControls data={data} workflow={workflow} />
    </>
  );
}

interface RagOverview {
  status: string;
  releaseId: string | null;
  releaseVersion: string | null;
  retrievalProfileId: string | null;
  candidateCount: number;
  adoptedCount: number;
  retrievalRunIds: string[];
}

function RagRuntimeOverview({ rag }: { rag: RagOverview }) {
  return (
    <section className="rag-runtime-overview">
      <header>
        <div><span className="eyebrow">RAG EVIDENCE</span><h2>本轮用了哪一版营养资料</h2></div>
        <span className={`rag-runtime-status control-${statusTone(rag.status)}`}>
          {statusLabel(rag.status)}
        </span>
      </header>
      <div className="rag-runtime-grid">
        <RagFact label="冻结 Release" value={rag.releaseVersion ?? "未记录版本"} detail={rag.releaseId} />
        <RagFact label="检索 Profile" value={rag.retrievalProfileId ?? "旧记录未冻结"} />
        <RagFact label="候选证据" value={`${rag.candidateCount} 条`} />
        <RagFact label="最终采用" value={`${rag.adoptedCount} 条`} />
      </div>
      <p>检索问题由营养 Agent 根据本轮专业问题生成；正文不在摘要重复展示，可在下方工程详情核对 Citation 采用链路。</p>
      {rag.retrievalRunIds.length > 0 && (
        <div className="rag-runtime-runs">
          <span>Retrieval Run</span>
          {rag.retrievalRunIds.map((runId) => <code key={runId}>{runId}</code>)}
        </div>
      )}
    </section>
  );
}

function RagFact({ label, value, detail }: { label: string; value: string; detail?: string | null }) {
  return <article><small>{label}</small><strong>{value}</strong>{detail && <code>{detail}</code>}</article>;
}

function ragOverview(workflow: WorkflowTraceView): RagOverview | null {
  const artifact = [...workflow.artifacts].reverse().find((item) => {
    const normalized = item.artifact_type.toLowerCase().replaceAll(/[^a-z0-9]/g, "");
    return normalized === "nutritioninputs" || normalized === "nutritionobservations";
  });
  if (!artifact?.payload) return null;
  const payload = recordValue(artifact.payload);
  const knowledge = recordValue(payload.knowledge);
  const snapshot = recordValue(payload.knowledge_snapshot);
  const candidates = arrayValue(knowledge.candidates);
  const citations = arrayValue(knowledge.citations);
  const retrievalRunIds = [...new Set(
    [...candidates, ...citations]
      .map((item) => stringOrNull(recordValue(item).retrieval_run_id))
      .filter((item): item is string => item !== null),
  )];
  return {
    status: stringOrNull(knowledge.corpus_status) ?? "unknown",
    releaseId: stringOrNull(snapshot.corpus_release_id)
      ?? firstRecordString(candidates, "corpus_release_id"),
    releaseVersion: stringOrNull(snapshot.corpus_release_version),
    retrievalProfileId: stringOrNull(snapshot.retrieval_profile_id),
    candidateCount: candidates.length,
    adoptedCount: citations.length,
    retrievalRunIds,
  };
}

function recordValue(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function arrayValue(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function stringOrNull(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function firstRecordString(values: unknown[], key: string): string | null {
  for (const value of values) {
    const found = stringOrNull(recordValue(value)[key]);
    if (found) return found;
  }
  return null;
}

function HarnessControls({ data, workflow }: { data: TraceDetail; workflow: WorkflowTraceView }) {
  const inputSafety = eventStatus(data, ["input_guard", "input_guarded"]);
  const contextFrozen = data.execution_summary.context_snapshot_count > 0;
  const outputSafety = eventStatus(data, ["output_guard", "output_guarded"]);
  const controls = [
    {
      title: "Turn 持久化",
      status: data.turn ? String(data.turn.status ?? "completed") : "unknown",
      detail: data.turn ? `Turn ${String(data.turn.id ?? "")}` : "未关联 Turn",
    },
    {
      title: "输入安全检查",
      status: inputSafety ?? "not_recorded",
      detail: inputSafety ? "已执行输入硬门检查" : "旧记录未提供独立事件",
    },
    {
      title: "上下文与权限冻结",
      status: contextFrozen ? "succeeded" : "not_recorded",
      detail: `${data.agent?.tool_count ?? 0} 个可用工具 · ${data.context_sources.length} 类上下文来源`,
    },
    {
      title: "Invocation Harness",
      status: workflow.invocations.every((item) => item.status === "succeeded") ? "succeeded" : workflow.status,
      detail: `${workflow.summary?.model_call_count ?? 0} 次模型 · ${workflow.summary?.total_token_count ?? 0} Token`,
    },
    {
      title: "输出保护",
      status: outputSafety ?? "not_modified",
      detail: outputSafety ? "已记录输出保护结果" : "没有记录到改写或拦截",
    },
    {
      title: "最终投递",
      status: data.trace.delivery_status,
      detail: data.output?.platform_msgid ? `平台消息 ${data.output.platform_msgid}` : "平台消息 ID 未记录",
    },
  ];
  return (
    <section className="harness-controls">
      <header><div><span className="eyebrow">HARNESS CONTROLS</span><h2>Harness 在这一轮负责了什么</h2></div><small>这里只展示可验证的控制动作</small></header>
      <div className="harness-control-grid">
        {controls.map((control) => (
          <article key={control.title}>
            <span className={`control-dot control-${statusTone(control.status)}`} />
            <div><strong>{control.title}</strong><p>{control.detail}</p></div>
            <small>{statusLabel(control.status)}</small>
          </article>
        ))}
      </div>
      <div className="harness-versions">
        <code>Core Prompt · {data.agent?.system_prompt_version ?? "未记录"}</code>
        <code>Context · {data.agent?.context_policy_version ?? "未记录"}</code>
        <code>Memory · {data.agent?.memory_policy_version ?? "未记录"}</code>
        <code>Graph · {workflow.graph_version ?? "未记录"}</code>
      </div>
    </section>
  );
}

function RouteNode({ label, detail, status }: { label: string; detail: string; status: string }) {
  return (
    <article className={`turn-route-node route-${statusTone(status)}`}>
      <span>{statusTone(status) === "good" ? "✓" : statusTone(status) === "bad" ? "!" : "·"}</span>
      <strong>{label}</strong>
      <small>{detail}</small>
    </article>
  );
}

function TraceTextCard({
  eyebrow,
  title,
  text,
  footer,
  tone = "plain",
}: {
  eyebrow: string;
  title: string;
  text: string;
  footer?: string | null;
  tone?: "plain" | "final";
}) {
  return (
    <article className={`trace-text-card trace-text-${tone}`}>
      <span className="eyebrow">{eyebrow}</span>
      <h3>{title}</h3>
      <p>{text}</p>
      {footer && <small>{footer}</small>}
    </article>
  );
}

function invocationDetail(role: string, attempt: number): string {
  const labels: Record<string, string> = {
    core: "理解任务并调用业务/专业工具",
    nutrition_expert: "结合证据与 RAG 形成专业评估",
    response_style: "使用同一已启用 Profile 整理表达",
    response_reviewer: "检查安全、依据与语义忠实度",
    dish_recognition: "观察图片中的菜品",
    nutrition_retrieval: "检索已发布营养资料",
    orchestrator: "历史工作流编排节点",
  };
  return `${labels[role] ?? "执行受限 Agent 任务"}${attempt > 1 ? ` · 第 ${attempt} 次返工` : ""}`;
}

function eventStatus(data: TraceDetail, operations: string[]): string | null {
  const event = [...data.timeline].reverse().find((item) => operations.includes(item.operation));
  return event?.status ?? null;
}

function statusTone(value: string): "good" | "warn" | "bad" {
  if (["succeeded", "completed", "accepted", "available", "delivered", "sent", "not_modified"].includes(value)) return "good";
  if (["error", "failed", "degraded", "rejected", "unavailable", "unknown"].includes(value)) return "bad";
  return "warn";
}

function statusLabel(value: string): string {
  const labels: Record<string, string> = {
    succeeded: "成功", completed: "完成", accepted: "已接收", delivered: "已送达",
    sent: "已发送", failed: "失败", degraded: "降级", running: "运行中",
    not_recorded: "未单独记录", not_modified: "无需拦截", unknown: "未知",
    pending: "等待中", pending_review: "待审核",
    available: "资料可用", empty: "没有可用资料", unavailable: "资料不可用", error: "检索失败",
  };
  return labels[value] ?? value;
}

function toolLabel(name: string): string {
  const labels: Record<string, string> = {
    consult_nutrition_specialist: "咨询营养专业 Agent",
    inspect_image: "查看图片",
    search_nutrition_knowledge: "检索营养知识库",
    record_weight: "记录体重",
    record_body_fat: "记录体脂",
    record_meal: "记录饮食",
    record_exercise: "记录运动",
    remember_long_term_memory: "保存长期记忆",
    forget_long_term_memory: "忘记长期记忆",
    clear_long_term_memories: "清空长期记忆",
  };
  return labels[name] ?? name;
}
