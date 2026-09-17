import type { TraceDetail } from "../../types";
import { agentRoleLabel, type WorkflowTraceView } from "./model";

type SummaryTone = "good" | "warn" | "bad" | "neutral";

interface SummaryCard {
  label: string;
  value: string;
  detail: string;
  tone: SummaryTone;
}

export function TraceOperatorSummary({
  data,
  workflow,
}: {
  data: TraceDetail;
  workflow: WorkflowTraceView;
}) {
  const failedInvocation = [...workflow.invocations]
    .reverse()
    .find((invocation) => invocation.status === "failed");
  const degraded = latestEventDetails(workflow, "response_degraded");
  const adopted = latestEventDetails(workflow, "response_adopted");
  const finalAdopted = adopted?.final === true;
  const delivered = ["accepted", "delivered", "sent", "succeeded"].includes(
    data.trace.delivery_status,
  );
  const multiAgentFailed = Boolean(failedInvocation || degraded || workflow.status === "failed");
  const usedFallback = multiAgentFailed && delivered;
  const failureCode =
    failedInvocation?.failure_code ?? stringValue(degraded?.reason_code) ?? data.trace.failure_code;
  const failureOwner = failedInvocation ? agentRoleLabel(failedInvocation.agent_role) : null;

  let title = "本次回复状态需要核对";
  let explanation = "当前记录不足以确定新流程是否用于最终回复。";
  let tone: SummaryTone = "neutral";
  if (["off", "legacy"].includes(workflow.mode) && !workflow.hasMultiAgentTrace) {
    title = delivered ? "用户已收到旧流程回复" : "旧流程回复尚未确认送达";
    explanation = "本轮没有运行 Multi-Agent，因此没有使用营养 RAG、医生风格或审查节点。";
    tone = delivered ? "neutral" : "warn";
  } else if (workflow.mode === "shadow") {
    title = delivered ? "用户已收到旧流程回复；新流程仅做影子评估" : "影子评估已运行";
    explanation = multiAgentFailed
      ? failureSentence(failureOwner, failureCode, true)
      : "影子候选不会发送给用户，用于上线前观察新流程表现。";
    tone = multiAgentFailed ? "warn" : "neutral";
  } else if (finalAdopted && delivered && !multiAgentFailed) {
    title = "本轮处理已完成并送达";
    explanation = "Core 主路径已完成必要的专业调用、风格处理和审查，最终文案已交给发送渠道。";
    tone = "good";
  } else if (usedFallback) {
    title = "消息已送达，但主路径发生降级";
    explanation = failureSentence(failureOwner, failureCode, true);
    tone = "bad";
  } else if (multiAgentFailed) {
    title = "本轮主路径执行失败";
    explanation = failureSentence(failureOwner, failureCode, false);
    tone = "bad";
  } else if (finalAdopted) {
    title = "最终回复已放行，等待确认送达";
    explanation = "最终 Artifact 已被采用，但渠道状态尚未确认送达。";
    tone = "warn";
  }

  const cards: SummaryCard[] = [
    {
      label: "最终回复",
      value: delivered ? "已送达" : deliveryLabel(data.trace.delivery_status),
      detail: usedFallback ? "实际发送安全降级文案" : finalAdopted ? "采用最终 Artifact" : "未记录最终采用",
      tone: delivered ? "good" : "warn",
    },
    workflowCard(workflow, multiAgentFailed, finalAdopted),
    ragCard(data, workflow, multiAgentFailed),
    stageCard(workflow, "response_style", "医生风格", multiAgentFailed),
    stageCard(workflow, "response_reviewer", "回复审查", multiAgentFailed),
  ];

  return (
    <section className={`operator-summary operator-summary-${tone}`}>
      <div className="operator-summary-copy">
        <span className="eyebrow">先看结论</span>
        <h3>{title}</h3>
        <p>{explanation}</p>
      </div>
      <div className="operator-summary-grid">
        {cards.map((card) => (
          <article className={`operator-summary-card summary-card-${card.tone}`} key={card.label}>
            <span>{card.label}</span>
            <strong>{card.value}</strong>
            <small>{card.detail}</small>
          </article>
        ))}
      </div>
    </section>
  );
}

function ragCard(
  data: TraceDetail,
  workflow: WorkflowTraceView,
  upstreamFailed: boolean,
): SummaryCard {
  const retrieval = workflow.invocations.filter(
    (invocation) => ["nutrition_retrieval", "nutrition_expert"].includes(invocation.agent_role),
  );
  if (retrieval.length > 0) {
    return invocationStageCard(retrieval, "营养 RAG");
  }
  if (data.trace.rag === true) {
    return {
      label: "营养 RAG",
      value: "已调用",
      detail: "知识检索记录在营养专业节点中",
      tone: "good",
    };
  }
  return {
    label: "营养 RAG",
    value: upstreamFailed ? "未执行到" : "本轮未调用",
    detail: upstreamFailed ? "流程在上游已中断" : "本轮问题不需要知识检索",
    tone: upstreamFailed ? "warn" : "neutral",
  };
}

function workflowCard(
  workflow: WorkflowTraceView,
  failed: boolean,
  finalAdopted: boolean,
): SummaryCard {
  if (["off", "legacy"].includes(workflow.mode) && !workflow.hasMultiAgentTrace) {
    return { label: "Agent 主路径", value: "未运行", detail: "本轮使用历史流程", tone: "neutral" };
  }
  if (workflow.mode === "shadow") {
    return {
      label: "Agent 主路径",
      value: failed ? "影子失败" : "仅影子运行",
      detail: "候选不会发送给用户",
      tone: failed ? "bad" : "neutral",
    };
  }
  if (failed) {
    return { label: "Agent 主路径", value: "已降级", detail: "查看明确的失败节点", tone: "bad" };
  }
  if (finalAdopted) {
    return { label: "Agent 主路径", value: "已完成", detail: "最终 Artifact 已采用", tone: "good" };
  }
  return { label: "Agent 主路径", value: "未确认", detail: "未记录最终采用事件", tone: "warn" };
}

function stageCard(
  workflow: WorkflowTraceView,
  role: "nutrition_retrieval" | "response_style" | "response_reviewer",
  label: string,
  upstreamFailed: boolean,
): SummaryCard {
  const invocations = workflow.invocations.filter((invocation) => invocation.agent_role === role);
  if (invocations.length === 0) {
    return {
      label,
      value: upstreamFailed ? "未执行到" : "本轮未调用",
      detail: upstreamFailed ? "流程在上游已中断" : "本轮路由未需要该节点",
      tone: upstreamFailed ? "warn" : "neutral",
    };
  }
  return invocationStageCard(invocations, label);
}

function invocationStageCard(
  invocations: WorkflowTraceView["invocations"],
  label: string,
): SummaryCard {
  const failed = invocations.some((invocation) => invocation.status === "failed");
  const degraded = invocations.some((invocation) => invocation.status === "degraded");
  if (failed) return { label, value: "失败", detail: "查看下方技术详情", tone: "bad" };
  if (degraded) return { label, value: "降级", detail: "执行完成但使用了降级结果", tone: "warn" };
  return { label, value: "已完成", detail: `${invocations.length} 次调用`, tone: "good" };
}

function failureSentence(owner: string | null, code: string | null, fallback: boolean): string {
  const ownerText = owner ? `${owner}失败：` : "失败原因：";
  const fallbackText = fallback ? "系统已使用安全降级回复。" : "本次没有确认送达。";
  return `${ownerText}${friendlyFailure(code)}。${fallbackText}`;
}

function friendlyFailure(code: string | null): string {
  if (!code) return "未记录具体原因";
  const labels: Record<string, string> = {
    token_budget_exhausted: "单个 Agent 的 Token 预算不足",
    turn_token_budget_exhausted: "整轮 Multi-Agent 的 Token 预算不足",
    turn_model_budget_exhausted: "整轮 Multi-Agent 的模型调用次数不足",
    invocation_budget_persistence_rejected: "调用结果超过预算，记录被拒绝",
    workflow_deadline_exceeded: "工作流执行超时",
    deadline_exceeded: "Agent 执行超时",
    workflow_cancelled: "工作流被取消",
    model_gateway_error: "模型服务调用异常",
    workflow_interrupted: "工作流被中断（旧记录没有保留更具体原因）",
    shadow_internal_error: "新流程发生内部错误",
  };
  return labels[code] ?? `系统错误（${code}）`;
}

function deliveryLabel(status: string): string {
  const labels: Record<string, string> = {
    failed: "发送失败",
    sending: "发送中",
    pending_review: "待审核",
    skipped: "未发送",
    deferred_external_session: "人工会话中",
  };
  return labels[status] ?? "未确认";
}

function latestEventDetails(
  workflow: WorkflowTraceView,
  operation: string,
): Record<string, unknown> | null {
  const event = [...workflow.timeline].reverse().find((item) => item.operation === operation);
  if (!event || event.details === null || typeof event.details !== "object" || Array.isArray(event.details)) {
    return null;
  }
  return event.details as Record<string, unknown>;
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}
