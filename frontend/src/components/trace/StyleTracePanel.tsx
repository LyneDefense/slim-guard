import type {
  TraceAgentArtifact,
  TraceResolvedStyleProfilePayload,
  TraceResponsePlanPayload,
  TraceStyledResponsePayload,
} from "../../types";
import { type WorkflowTraceView } from "./model";

interface StyleTraceView {
  profileName: string | null;
  profileVersion: string | null;
  planArtifact: TraceAgentArtifact | null;
  renderedArtifact: TraceAgentArtifact | null;
  adoptedArtifactId: string | null;
  adoptionFinal: boolean | null;
  blocks: Array<{ kind: string; count: number; sourceCount: number }>;
  totalSourceCount: number;
  renderedCharacterCount: number | null;
  usedBlockCount: number;
  styleInvocationStatus: string | null;
  degradedReason: string | null;
  degradedFallback: string | null;
  degradedArtifactId: string | null;
  bypassReason: string | null;
}

export function StyleTracePanel({ workflow }: { workflow: WorkflowTraceView }) {
  const view = buildStyleTrace(workflow);
  if (!view) return null;
  const renderer = view.styleInvocationStatus
    ? "Style Agent"
    : view.renderedArtifact
      ? "Coordinator 中性渲染"
      : view.bypassReason
        ? "风格层已绕过"
        : "Style 未运行";
  return (
    <section className="style-trace-panel">
      <div className="section-heading style-trace-heading">
        <div>
          <h2>回复风格处理</h2>
          <p>展示风格输入的结构和采用关系；内容块正文默认不在这里展开。</p>
        </div>
        <span className="style-renderer-badge">{renderer}</span>
      </div>
      <div className="style-profile-strip">
        <div><span>Style Profile</span><strong>{view.profileName ?? "未单独记录"}</strong></div>
        <div><span>Profile 版本</span><code>{view.profileVersion ?? "未记录"}</code></div>
        <div><span>执行状态</span><strong>{styleStatusLabel(view.styleInvocationStatus)}</strong></div>
      </div>
      {view.blocks.length > 0 && (
        <div className="style-block-summary">
          <header>
            <div><h3>进入 Style 的内容块</h3><p>仅显示类型、数量和来源引用数，不显示用户敏感原文。</p></div>
            <span>{view.blocks.reduce((total, block) => total + block.count, 0)} 块 · {view.totalSourceCount} 个来源引用</span>
          </header>
          <div>
            {view.blocks.map((block) => (
              <article key={block.kind}>
                <strong>{contentBlockLabel(block.kind)}</strong>
                <span>{block.count} 块</span>
                <small>{block.sourceCount} 个来源引用</small>
              </article>
            ))}
          </div>
        </div>
      )}
      <div className="style-artifact-flow">
        <ArtifactStep label="结构化输入" artifact={view.planArtifact} detail={view.planArtifact ? "ResponsePlan" : "未记录"} />
        <span className="style-flow-arrow">→</span>
        <ArtifactStep
          label="风格渲染"
          artifact={view.renderedArtifact}
          detail={view.renderedCharacterCount == null ? "未记录" : `${view.renderedCharacterCount} 字 · 使用 ${view.usedBlockCount} 块`}
        />
        <span className="style-flow-arrow">→</span>
        <ArtifactStep
          label={view.adoptionFinal === false ? "Shadow 候选" : view.adoptionFinal ? "最终采用" : "采用状态"}
          artifactId={view.adoptedArtifactId}
          detail={view.adoptionFinal === false ? "未发送给用户" : view.adoptionFinal ? "进入最终输出" : "未记录采用事件"}
        />
      </div>
      {view.degradedReason && (
        <div className="style-notice style-notice-degraded">
          <strong>风格渲染已降级</strong>
          <span>{view.degradedReason} · {view.degradedFallback ?? "fallback 未记录"}</span>
          {view.degradedArtifactId && <code>{view.degradedArtifactId}</code>}
        </div>
      )}
      {view.bypassReason && (
        <div className="style-notice style-notice-bypass">
          <strong>本轮绕过风格层</strong><span>{bypassReasonLabel(view.bypassReason)}</span>
        </div>
      )}
    </section>
  );
}

function ArtifactStep({
  label,
  artifact,
  artifactId,
  detail,
}: {
  label: string;
  artifact?: TraceAgentArtifact | null;
  artifactId?: string | null;
  detail: string;
}) {
  const id = artifact?.artifact_id ?? artifactId ?? null;
  return (
    <article className={id ? "style-artifact" : "style-artifact style-artifact-empty"}>
      <span>{label}</span><strong>{artifact?.artifact_type ?? (id ? "Response Artifact" : "—")}</strong>
      <small>{detail}</small><code>{id ?? "无 Artifact"}</code>
    </article>
  );
}

function buildStyleTrace(workflow: WorkflowTraceView): StyleTraceView | null {
  const planArtifact = latestArtifact(workflow.artifacts, "responseplan");
  const renderedArtifact = latestArtifact(workflow.artifacts, "styledresponse")
    ?? latestArtifact(workflow.artifacts, "neutralresponse");
  const profileArtifact = latestArtifact(workflow.artifacts, "styleresolution")
    ?? latestArtifact(workflow.artifacts, "resolvedstyleprofile")
    ?? latestArtifact(workflow.artifacts, "styleprofile");
  const styleInvocation = [...workflow.invocations]
    .reverse()
    .find((invocation) => invocation.agent_role === "response_style");
  const adopted = latestEvent(workflow, "response_adopted");
  const degraded = latestEvent(workflow, "response_degraded");
  const bypass = latestEvent(workflow, "style_bypass");
  const outputGuard = latestEvent(workflow, "output_guard");
  const turnFinished = latestEvent(workflow, "turn_finished");
  const bypassTransition = [...workflow.transitions].reverse().find((transition) =>
    transition.transition_type === "skip" &&
    ["safety_bypass", "operational_bypass"].includes(transition.reason_code),
  );
  const plan = payloadAs<TraceResponsePlanPayload>(planArtifact);
  const styled = payloadAs<TraceStyledResponsePayload>(renderedArtifact);
  const profile = payloadAs<TraceResolvedStyleProfilePayload>(profileArtifact);
  const blocks = summarizeBlocks(plan.content_blocks);
  const adoptedArtifactId = stringValue(adopted?.artifact_id)
    ?? workflow.shadowComparison?.candidate.artifact_id
    ?? null;
  const degradedFallback = stringValue(degraded?.fallback_type);
  const bypassReason = stringValue(bypass?.reason_code)
    ?? stringValue(bypass?.bypass_reason)
    ?? bypassTransition?.reason_code
    ?? findBypassReason(workflow.artifacts)
    ?? (degradedFallback === "style_bypass" ? stringValue(degraded?.reason_code) : null)
    ?? (!styleInvocation && stringValue(outputGuard?.code) !== null ? "safety_bypass" : null)
    ?? (!styleInvocation && isOperationalTermination(turnFinished) ? "operational_bypass" : null);
  const hasStyleEvidence = Boolean(
    planArtifact || renderedArtifact || profileArtifact || styleInvocation || bypass ||
    bypassTransition || (degraded && isStyleDegradation(degraded)),
  );
  if (!hasStyleEvidence) return null;
  const profileVersion = profile.version ?? profile.style_profile_version
    ?? profileArtifact?.style_profile_version
    ?? styled.style_profile_version ?? renderedArtifact?.style_profile_version ?? null;

  return {
    profileName: profile.display_name ?? profile.profile_name ?? profile.name ?? profile.profile_id
      ?? profileDisplayName(profileVersion),
    profileVersion,
    planArtifact,
    renderedArtifact,
    adoptedArtifactId,
    adoptionFinal: booleanValue(adopted?.final),
    blocks,
    totalSourceCount: blocks.reduce((total, block) => total + block.sourceCount, 0),
    renderedCharacterCount: typeof styled.text === "string" ? styled.text.length : null,
    usedBlockCount: stringArray(styled.used_block_ids).length,
    styleInvocationStatus: styleInvocation?.status ?? null,
    degradedReason: isStyleDegradation(degraded) ? stringValue(degraded?.reason_code) : null,
    degradedFallback: isStyleDegradation(degraded) ? degradedFallback : null,
    degradedArtifactId: isStyleDegradation(degraded) ? nullableString(degraded?.artifact_id) : null,
    bypassReason,
  };
}

function summarizeBlocks(
  blocks: TraceResponsePlanPayload["content_blocks"],
): Array<{ kind: string; count: number; sourceCount: number }> {
  const summary = new Map<string, { kind: string; count: number; sourceCount: number }>();
  for (const block of Array.isArray(blocks) ? blocks : []) {
    const kind = typeof block.kind === "string" ? block.kind : "unknown";
    const current = summary.get(kind) ?? { kind, count: 0, sourceCount: 0 };
    current.count += 1;
    current.sourceCount += stringArray(block.source_refs).length;
    summary.set(kind, current);
  }
  return [...summary.values()];
}

function latestArtifact(
  artifacts: TraceAgentArtifact[],
  normalizedType: string,
): TraceAgentArtifact | null {
  return [...artifacts].reverse().find((artifact) =>
    artifact.artifact_type.toLowerCase().replaceAll(/[^a-z0-9]/g, "") === normalizedType,
  ) ?? null;
}

function payloadAs<T extends object>(artifact: TraceAgentArtifact | null): Partial<T> {
  return artifact?.payload && typeof artifact.payload === "object" ? artifact.payload as Partial<T> : {};
}

function latestEvent(
  workflow: WorkflowTraceView,
  operation: string,
): Record<string, unknown> | null {
  const event = workflow.timeline
    .filter((item) => item.operation === operation)
    .sort((left, right) => left.sequence - right.sequence)
    .at(-1);
  return event ? asRecord(event.details) : null;
}

function findBypassReason(artifacts: TraceAgentArtifact[]): string | null {
  for (const artifact of [...artifacts].reverse()) {
    const payload = asRecord(artifact.payload);
    const reason = stringValue(payload.style_bypass_reason) ?? stringValue(payload.bypass_reason);
    if (reason) return reason;
  }
  return null;
}

function isStyleDegradation(details: Record<string, unknown> | null): boolean {
  if (!details) return false;
  const marker = `${stringValue(details.reason_code) ?? ""} ${stringValue(details.fallback_type) ?? ""}`;
  return /style|neutral_renderer/.test(marker.toLowerCase());
}

function isOperationalTermination(details: Record<string, unknown> | null): boolean {
  const termination = stringValue(details?.termination);
  return termination === "waiting_user_confirmation" || termination === "waiting_human_review";
}

function contentBlockLabel(kind: string): string {
  const labels: Record<string, string> = {
    fact: "事实",
    claim: "专业结论",
    action: "行动建议",
    question: "追问",
    risk: "风险提示",
    uncertainty: "不确定性",
    social_act: "日常沟通",
  };
  return labels[kind] ?? kind;
}

function profileDisplayName(version: string | null): string | null {
  if (version === "slimguard_default_v1") return "SlimGuard 默认风格";
  if (version === "neutral_shadow_v1") return "Shadow 中性风格";
  return null;
}

function styleStatusLabel(status: string | null): string {
  const labels: Record<string, string> = {
    started: "处理中",
    succeeded: "成功",
    degraded: "已降级",
    failed: "失败",
  };
  return status ? labels[status] ?? status : "未调用 Style Agent";
}

function bypassReasonLabel(reason: string): string {
  const labels: Record<string, string> = {
    safety_bypass: "安全响应使用专用模板，不经过普通风格渲染。",
    operational_bypass: "操作性系统文本使用专用模板，不经过普通风格渲染。",
  };
  return labels[reason] ?? reason;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function nullableString(value: unknown): string | null {
  return value == null ? null : stringValue(value);
}

function booleanValue(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}
