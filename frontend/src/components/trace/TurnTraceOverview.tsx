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
  if (["succeeded", "completed", "accepted", "delivered", "sent", "not_modified"].includes(value)) return "good";
  if (["failed", "degraded", "rejected", "unknown"].includes(value)) return "bad";
  return "warn";
}

function statusLabel(value: string): string {
  const labels: Record<string, string> = {
    succeeded: "成功", completed: "完成", accepted: "已接收", delivered: "已送达",
    sent: "已发送", failed: "失败", degraded: "降级", running: "运行中",
    not_recorded: "未单独记录", not_modified: "无需拦截", unknown: "未知",
    pending: "等待中", pending_review: "待审核",
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
