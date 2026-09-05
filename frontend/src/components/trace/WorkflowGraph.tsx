import type { AgentRole, TraceWorkflowTransition } from "../../types";
import {
  agentRoleLabel,
  type AgentInvocationView,
  type WorkflowTraceView,
} from "./model";

const NODE_LABELS: Record<string, string> = {
  context_ready: "上下文就绪",
  orchestrator_running: "对话编排",
  nutrition_running: "营养分析",
  response_rendering: "生成回复计划",
  style_resolved: "风格策略就绪",
  style_running: "表达风格",
  review_running: "忠实度审查",
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
          <p>只展示真实执行过的节点；箭头来自 Coordinator 保存的实际转换。</p>
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
                  <span>→</span>
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
    return workflow.invocations.map((invocation, index) => ({
      key: invocation.invocation_id,
      node: nodeForRole(invocation.agent_role),
      invocation,
      incoming: index === 0 ? null : inferredTransition(workflow.invocations[index - 1], invocation),
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

function inferredTransition(
  from: AgentInvocationView,
  to: AgentInvocationView,
): TraceWorkflowTransition {
  return {
    from_node: nodeForRole(from.agent_role),
    to_node: nodeForRole(to.agent_role),
    transition_type: to.repaired ? "repair" : "forward",
    reason_code: to.parent_invocation_id === from.invocation_id ? "parent_invocation" : "invocation_sequence",
    attempt: to.attempt,
  };
}

function roleForNode(node: string): AgentRole | null {
  const roles: Partial<Record<string, AgentRole>> = {
    orchestrator: "orchestrator",
    orchestrator_running: "orchestrator",
    nutrition_expert: "nutrition_expert",
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
    nutrition_expert: "nutrition_running",
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
    context_request: "补充上下文",
    degraded: "降级",
    fallback: "转入兜底",
  };
  const type = labels[transition.transition_type] ?? transition.transition_type;
  return `${type} · ${transition.reason_code}`;
}

function isRepair(transition: TraceWorkflowTransition): boolean {
  return transition.attempt > 1 || /repair|retry|return/.test(transition.transition_type);
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
