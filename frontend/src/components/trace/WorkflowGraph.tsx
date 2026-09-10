import type { AgentRole, TraceWorkflowTransition } from "../../types";
import {
  agentRoleLabel,
  type AgentInvocationView,
  type WorkflowTraceView,
} from "./model";

const NODE_LABELS: Record<string, string> = {
  initialized: "工作流初始化",
  input_guarded: "输入保护",
  memory_ingested: "记忆摄取",
  memory_recall: "记忆召回",
  context_ready: "上下文就绪",
  orchestrator_running: "对话编排",
  dish_recognition_running: "菜品识别",
  dish_confirmation_pending: "等待确认菜名",
  dish_confirmation_resolved: "菜名已确认",
  nutrition_retrieval_running: "营养证据检索",
  nutrition_evidence_ready: "营养证据就绪",
  evidence_ready: "证据包就绪",
  expert_running: "营养分析",
  nutrition_running: "营养分析",
  response_rendering: "生成回复计划",
  style_resolved: "风格策略就绪",
  style_running: "表达风格",
  review_running: "忠实度审查",
  neutral_fallback: "中性降级输出",
  output_guarded: "输出保护",
  completed: "工作流完成",
  degraded: "降级输出",
  failed: "工作流失败",
};

interface GraphStep {
  key: string;
  node: string;
  invocation: AgentInvocationView | null;
  incoming: TraceWorkflowTransition | null;
}

export function WorkflowGraph({ workflow }: { workflow: WorkflowTraceView }) {
  const steps = buildSteps(workflow);
  if (steps.length === 0) return null;
  return (
    <section className="workflow-section">
      <div className="section-heading workflow-heading">
        <div>
          <h2>本轮实际工作流</h2>
          <p>{workflow.transitions.length > 0
            ? "只展示真实执行过的节点；箭头来自 Coordinator 保存的实际转换。"
            : "按 Invocation 顺序展示已执行节点；旧 Trace 未记录工作流转换。"}</p>
        </div>
        <div className="workflow-summary-badges">
          <span>{workflow.mode.toUpperCase()}</span>
          {workflow.graph_version && <code>{workflow.graph_version}</code>}
          <WorkflowStatus value={workflow.status} />
        </div>
      </div>
      <div className="workflow-viewport">
        <div className="workflow-track">
          {steps.map((step, index) => (
            <div className="workflow-step" key={step.key}>
              {step.incoming && (
                <div className={`workflow-edge ${isRepair(step.incoming) ? "workflow-edge-repair" : ""}`}>
                  <span aria-label={isRepair(step.incoming) ? "返回修复" : "继续"}>
                    {isRepair(step.incoming) ? "↩" : "→"}
                  </span>
                  <small>{transitionLabel(step.incoming)}</small>
                </div>
              )}
              <article className={`workflow-node workflow-node-${statusTone(step.invocation?.status)}`}>
                <header>
                  <span className="workflow-node-number">{index + 1}</span>
                  <small>{step.invocation ? "AGENT" : "COORDINATOR"}</small>
                </header>
                <h3>{step.invocation ? agentRoleLabel(step.invocation.agent_role) : nodeLabel(step.node)}</h3>
                <code>{step.node}</code>
                {step.invocation ? (
                  <dl>
                    <div><dt>状态</dt><dd><WorkflowStatus value={step.invocation.status} /></dd></div>
                    <div><dt>尝试</dt><dd>{step.invocation.attempt}{step.invocation.repaired ? " · 修复" : ""}</dd></div>
                    <div><dt>耗时</dt><dd>{formatDuration(step.invocation.duration_ms)}</dd></div>
                    <div><dt>工具</dt><dd>{step.invocation.tool_call_count}</dd></div>
                  </dl>
                ) : (
                  <p>协调器已实际经过此状态。</p>
                )}
              </article>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function buildSteps(workflow: WorkflowTraceView): GraphStep[] {
  if (workflow.transitions.length === 0) {
    return workflow.invocations.map((invocation) => ({
      key: invocation.invocation_id,
      node: nodeForRole(invocation.agent_role),
      invocation,
      incoming: null,
    }));
  }

  const nodes: Array<{ node: string; incoming: TraceWorkflowTransition | null }> = [];
  for (const transition of workflow.transitions) {
    const previous = nodes.at(-1);
    if (!previous || previous.node !== transition.from_node) {
      nodes.push({ node: transition.from_node, incoming: null });
    }
    nodes.push({ node: transition.to_node, incoming: transition });
  }
  const available = [...workflow.invocations];
  return nodes.map((item, index) => {
    const role = roleForNode(item.node);
    const invocationIndex = role
      ? available.findIndex((invocation) => invocation.agent_role === role)
      : -1;
    const invocation = invocationIndex >= 0 ? available.splice(invocationIndex, 1)[0] : null;
    return {
      key: `${index}-${item.node}-${invocation?.invocation_id ?? "control"}`,
      node: item.node,
      invocation,
      incoming: item.incoming,
    };
  });
}

function roleForNode(node: string): AgentRole | null {
  const roles: Partial<Record<string, AgentRole>> = {
    orchestrator: "orchestrator",
    orchestrator_running: "orchestrator",
    dish_recognition_running: "dish_recognition",
    nutrition_retrieval_running: "nutrition_retrieval",
    nutrition_expert: "nutrition_expert",
    expert_running: "nutrition_expert",
    nutrition_running: "nutrition_expert",
    response_style: "response_style",
    style_running: "response_style",
    response_reviewer: "response_reviewer",
    review_running: "response_reviewer",
  };
  return roles[node] ?? null;
}

function nodeForRole(role: AgentRole): string {
  return {
    orchestrator: "orchestrator_running",
    dish_recognition: "dish_recognition_running",
    nutrition_retrieval: "nutrition_retrieval_running",
    nutrition_expert: "expert_running",
    response_style: "style_running",
    response_reviewer: "review_running",
  }[role];
}

function nodeLabel(node: string): string {
  return NODE_LABELS[node] ?? node.replaceAll("_", " ");
}

function transitionLabel(transition: TraceWorkflowTransition): string {
  const labels: Record<string, string> = {
    forward: "继续",
    repair: "返回修复",
    return: "返回修复",
    context_request: "补充上下文",
    degraded: "降级",
    fallback: "转入兜底",
  };
  const type = labels[transition.transition_type] ?? transition.transition_type;
  return `${type} · ${transition.reason_code}`;
}

function isRepair(transition: TraceWorkflowTransition): boolean {
  const marker = `${transition.transition_type} ${transition.reason_code}`.toLowerCase();
  return ["repair", "return", "retry"].includes(transition.transition_type)
    || (["review_running", "review", "response_reviewer"].includes(transition.from_node)
      && /repair|retry|return/.test(marker));
}

function statusTone(status: string | undefined): string {
  if (status === "failed") return "failed";
  if (status === "degraded") return "degraded";
  if (status === "running" || status === "started") return "running";
  return "succeeded";
}

function WorkflowStatus({ value }: { value: string }) {
  const labels: Record<string, string> = {
    succeeded: "成功",
    completed: "完成",
    running: "运行中",
    started: "运行中",
    degraded: "已降级",
    failed: "失败",
    unknown: "未知",
  };
  return <span className={`workflow-status workflow-status-${statusTone(value)}`}>{labels[value] ?? value}</span>;
}

function formatDuration(value: number | null): string {
  if (value == null) return "—";
  if (value < 1000) return `${value} ms`;
  return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)} s`;
}
