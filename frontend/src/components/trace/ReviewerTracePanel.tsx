import { useQuery } from "@tanstack/react-query";

import { api } from "../../api";
import type {
  TraceAgentArtifact,
  TraceReviewerArtifactRef,
  TraceWorkflowReviewMetrics,
  TraceWorkflowTransition,
} from "../../types";
import { agentRoleLabel, type AgentInvocationView, type WorkflowTraceView } from "./model";

interface VerdictView {
  key: string;
  artifactId: string | null;
  invocationId: string | null;
  attempt: number;
  verdict: string;
  repairTarget: string | null;
  issueTypes: string[];
  reasonSummaryPresent: boolean;
  reviewedArtifactIds: string[];
  createdAt: string | null;
}

interface RepairAttemptView {
  key: string;
  attempt: number;
  target: string | null;
  status: string | null;
  inputArtifactId: string | null;
  outputArtifactId: string | null;
  verdictArtifactId: string | null;
  transition: TraceWorkflowTransition | null;
}

interface ArtifactView {
  id: string | null;
  artifact: TraceAgentArtifact | null;
  summaryRef: TraceReviewerArtifactRef | null;
}

interface ComparisonView {
  original: ArtifactView;
  repaired: ArtifactView[];
  finalAdopted: ArtifactView;
  changed: boolean | null;
}

interface RateView {
  label: string;
  rate: number | null;
  detail: string;
  tone: string;
}

export function ReviewerTracePanel({ workflow }: { workflow: WorkflowTraceView }) {
  const hasReviewer = hasReviewerEvidence(workflow);
  const metricsQuery = useQuery({
    queryKey: ["workflow-review-metrics", 7],
    queryFn: () => api.workflowMetrics(7),
    enabled: hasReviewer,
    retry: false,
    staleTime: 5 * 60_000,
  });
  if (!hasReviewer) return null;

  const summary = asRecord(workflow.reviewSummary);
  const reviewerInvocations = workflow.invocations.filter(
    (invocation) => invocation.agent_role === "response_reviewer",
  );
  const verdicts = collectVerdicts(workflow, summary);
  const latestVerdict = verdicts.at(-1) ?? null;
  const repairTransitions = workflow.transitions.filter(isReviewRepairTransition);
  const repairAttempts = repairAttemptRecords(summary, repairTransitions);
  const repairUsed = firstNumber(
    asRecord(summary.repair_counts).total,
    asRecord(summary.budget).repair_attempts_observed,
    summary.repair_count,
    workflow.summary?.repair_count,
    repairAttempts.length,
  ) ?? 0;
  const repairLimit = configuredRepairLimit(summary, workflow);
  const budget = asRecord(summary.budget);
  const exhausted = booleanValue(budget.exhausted)
    ?? (repairLimit !== null ? repairUsed >= repairLimit : false);
  const comparison = buildComparison(workflow, summary, reviewerInvocations, verdicts);
  const rates = buildRates(workflow, summary, verdicts, metricsQuery.data ?? null);

  return (
    <section className="reviewer-panel">
      <div className="section-heading reviewer-heading">
        <div>
          <h2>Reviewer 忠实度审查</h2>
          <p>显示结构化 Verdict、有限返回边与 Artifact 采用关系，不展示回复或问题正文。</p>
        </div>
        <VerdictBadge value={latestVerdict?.verdict ?? stringValue(summary.status) ?? "unknown"} />
      </div>

      <div className="reviewer-rates">
        {rates.map((rate) => (
          <article className={"reviewer-rate reviewer-rate-" + rate.tone} key={rate.label}>
            <strong>{formatRate(rate.rate)}</strong>
            <span>{rate.label}</span>
            <small>{rate.detail}</small>
          </article>
        ))}
      </div>
      {!metricsQuery.data && (
        <p role="status">{metricsQuery.isError
          ? "近 7 天聚合指标暂不可用，当前比例仅代表当前 Trace。"
          : "正在读取近 7 天聚合指标，当前比例暂时仅代表当前 Trace。"}</p>
      )}

      <div className={"review-budget" + (exhausted ? " review-budget-exhausted" : "")}>
        <div>
          <span>修复尝试 / 预算</span>
          <strong>{repairUsed} / {repairLimit ?? "上限未记录"}</strong>
        </div>
        {repairLimit !== null && (
          <div
            aria-label={"已使用 " + repairUsed + "，上限 " + repairLimit}
            className="review-budget-meter"
          >
            <span
              style={{
                width: String(Math.min(
                  100,
                  repairLimit > 0 ? (repairUsed / repairLimit) * 100 : 100,
                )) + "%",
              }}
            />
          </div>
        )}
        <small>{budgetMessage(budget, exhausted, repairUsed, repairLimit)}</small>
      </div>

      <VerdictHistory
        reviewerInvocationCount={reviewerInvocations.length}
        verdicts={verdicts}
      />
      <RepairAttempts attempts={repairAttempts} />

      <section className="review-artifact-comparison">
        <header>
          <div>
            <h3>审查前 → 修复后 → 最终采用</h3>
            <p>只展示 Artifact 元数据和结构字段，不展示敏感正文。</p>
          </div>
          {comparison.changed !== null && (
            <span>{comparison.changed ? "Artifact 已变化" : "未经内容替换"}</span>
          )}
        </header>
        <div className="review-artifact-flow">
          <ArtifactColumn
            artifacts={[comparison.original]}
            empty="未记录审查前 Artifact"
            label="审查前 Artifact"
          />
          <span className="review-artifact-arrow">→</span>
          <ArtifactColumn
            artifacts={comparison.repaired}
            empty={repairUsed > 0 ? "修复 Artifact 引用缺失" : "本轮未触发修复"}
            label="修复 Artifact"
          />
          <span className="review-artifact-arrow">→</span>
          <ArtifactColumn
            artifacts={[comparison.finalAdopted]}
            empty={workflow.mode === "shadow" ? "Shadow 候选未作为最终输出采用" : "未记录最终采用 Artifact"}
            label="最终采用 Artifact"
          />
        </div>
      </section>
    </section>
  );
}

function VerdictHistory({
  verdicts,
  reviewerInvocationCount,
}: {
  verdicts: VerdictView[];
  reviewerInvocationCount: number;
}) {
  return (
    <section className="review-verdicts">
      <header>
        <div>
          <h3>Reviewer Verdict</h3>
          <p>{reviewerInvocationCount} 次 Reviewer Invocation · {verdicts.length} 个可审计 Verdict</p>
        </div>
      </header>
      {verdicts.length > 0 ? (
        <div className="review-verdict-list">
          {verdicts.map((verdict) => (
            <article className={"review-verdict review-verdict-" + verdict.verdict} key={verdict.key}>
              <header>
                <div><span>Attempt {verdict.attempt}</span><VerdictBadge value={verdict.verdict} /></div>
                <code>{verdict.artifactId ?? "Verdict Artifact 未记录"}</code>
              </header>
              <dl>
                <div><dt>issue_type</dt><dd>{verdict.issueTypes.length > 0
                  ? verdict.issueTypes.map((issue) => <code key={issue}>{issueTypeLabel(issue)}</code>)
                  : "无"}</dd>
                </div>
                <div><dt>repair_target</dt><dd>{verdict.repairTarget
                  ? repairTargetLabel(verdict.repairTarget)
                  : "无"}</dd>
                </div>
                <div><dt>reason_summary</dt><dd>{verdict.reasonSummaryPresent
                  ? "已记录，正文按隐私策略隐藏" : "未记录"}</dd>
                </div>
                <div><dt>被审查 Artifact</dt><dd>{verdict.reviewedArtifactIds.length > 0
                  ? verdict.reviewedArtifactIds.join("、")
                  : "未记录"}</dd>
                </div>
              </dl>
            </article>
          ))}
        </div>
      ) : (
        <div className="review-empty">Reviewer 已运行，但旧 Trace 没有可解析的 Verdict payload。</div>
      )}
    </section>
  );
}

function RepairAttempts({ attempts }: { attempts: RepairAttemptView[] }) {
  if (attempts.length === 0) return null;
  return (
    <section className="review-repairs">
      <h3>有限返回 / Repair Attempts</h3>
      <div>
        {attempts.map((attempt) => (
          <article key={attempt.key}>
            <header>
              <strong>Attempt {attempt.attempt}</strong>
              <span>{attempt.target ? repairTargetLabel(attempt.target) : "目标未记录"}</span>
              <small>{attempt.status ?? "状态未记录"}</small>
            </header>
            {attempt.transition && (
              <p>
                ↩ {attempt.transition.from_node} → {attempt.transition.to_node}
                {" · " + attempt.transition.reason_code}
              </p>
            )}
            <div>
              <code>input · {attempt.inputArtifactId ?? "—"}</code>
              <code>output · {attempt.outputArtifactId ?? "—"}</code>
              <code>verdict · {attempt.verdictArtifactId ?? "—"}</code>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function ArtifactColumn({
  label,
  artifacts,
  empty,
}: {
  label: string;
  artifacts: ArtifactView[];
  empty: string;
}) {
  const visible = artifacts.filter((artifact) => artifact.id !== null);
  return (
    <section className="review-artifact-column">
      <h4>{label}</h4>
      {visible.length > 0
        ? visible.map((artifact, index) => (
          <ArtifactSummaryCard artifact={artifact} key={(artifact.id ?? "missing") + "-" + index} />
        ))
        : <div className="review-artifact-empty">{empty}</div>}
    </section>
  );
}

function ArtifactSummaryCard({ artifact }: { artifact: ArtifactView }) {
  const details = artifact.artifact;
  const summary = artifact.summaryRef;
  const type = details?.artifact_type ?? nullableString(summary?.artifact_type);
  const producer = details?.producer_role ?? nullableString(summary?.producer_role);
  const schema = details?.schema_version ?? nullableString(summary?.schema_version);
  const integrity = details?.integrity_status ?? nullableString(summary?.integrity_status);
  const fields = safePayloadFields(details?.payload);
  return (
    <article className="review-artifact-card">
      <header><strong>{type ?? "Artifact 类型未记录"}</strong><span>{integrity ?? "完整性未记录"}</span></header>
      <dl>
        <div><dt>生产者</dt><dd>{producer ? agentRoleLabel(producer) : "未记录"}</dd></div>
        <div><dt>Schema</dt><dd>{schema ?? "未记录"}</dd></div>
        <div><dt>父 Artifact</dt><dd>{details?.parent_artifact_ids.length ?? "未记录"}</dd></div>
        <div><dt>安全结构字段</dt><dd>{fields.length > 0 ? fields.join("、") : "正文已脱敏或无 payload"}</dd></div>
      </dl>
      <code>{artifact.id}</code>
    </article>
  );
}

function collectVerdicts(
  workflow: WorkflowTraceView,
  summary: Record<string, unknown>,
): VerdictView[] {
  const reviewerInvocations = workflow.invocations.filter(
    (invocation) => invocation.agent_role === "response_reviewer",
  );
  const sources: Array<{ value: Record<string, unknown>; artifactId: string | null }> = [];
  for (const artifact of workflow.artifacts) {
    const normalized = normalize(artifact.artifact_type);
    const isReviewerOutput = reviewerInvocations.some(
      (invocation) => invocation.output_artifact_id === artifact.artifact_id,
    );
    if (!isReviewerOutput && !["reviewerverdict", "reviewverdict", "fidelityverdict"].includes(normalized)) {
      continue;
    }
    sources.push({ value: asRecord(artifact.payload), artifactId: artifact.artifact_id });
  }
  for (const value of [
    ...objectArray(summary.verdicts),
    ...objectArray(summary.attempts),
  ]) {
    sources.push({ value, artifactId: nullableString(value.artifact_id) });
  }
  const latest = asRecord(summary.latest_verdict);
  if (Object.keys(latest).length > 0) {
    sources.push({ value: latest, artifactId: nullableString(latest.artifact_id) });
  }
  for (const event of workflow.timeline) {
    if (!["reviewer_verdict", "review_verdict"].includes(event.operation)) continue;
    const details = asRecord(event.details);
    sources.push({ value: details, artifactId: nullableString(details.artifact_id) });
  }

  const unique = new Map<string, VerdictView>();
  sources.forEach((source, index) => {
    const verdict = parseVerdict(source.value, source.artifactId, reviewerInvocations, index);
    if (verdict) unique.set(verdict.key, verdict);
  });
  return [...unique.values()].sort(
    (left, right) => left.attempt - right.attempt || compareDates(left.createdAt, right.createdAt),
  );
}

function parseVerdict(
  value: Record<string, unknown>,
  fallbackArtifactId: string | null,
  invocations: AgentInvocationView[],
  index: number,
): VerdictView | null {
  const verdict = firstString(value.verdict, value.outcome);
  if (!verdict) return null;
  const artifactId = firstString(value.artifact_id, fallbackArtifactId);
  const invocationId = nullableString(value.invocation_id)
    ?? invocations.find((invocation) => invocation.output_artifact_id === artifactId)?.invocation_id
    ?? null;
  const invocation = invocations.find((item) => item.invocation_id === invocationId);
  const issues = objectArray(value.issues);
  const issueTypes = uniqueStrings([
    ...stringArray(value.issue_types),
    ...stringArray(value.issue_codes),
    ...[value.issue_type],
    ...issues.flatMap((issue) => [issue.type, issue.issue_type, issue.code]),
  ]);
  const attempt = firstNumber(value.attempt, value.repair_attempt, invocation?.attempt, index + 1) ?? 1;
  return {
    key: artifactId ?? (invocationId ?? "review") + "-" + attempt + "-" + verdict,
    artifactId,
    invocationId,
    attempt,
    verdict,
    repairTarget: firstString(value.repair_target, value.target, value.target_agent),
    issueTypes,
    reasonSummaryPresent: value.reason_summary_present === true
      || firstString(value.reason_summary, value.reason, value.explanation) !== null,
    reviewedArtifactIds: uniqueStrings([
      ...stringArray(value.reviewed_artifact_ids),
      ...stringArray(value.input_artifact_ids),
      value.candidate_artifact_id,
    ]),
    createdAt: nullableString(value.created_at) ?? invocation?.completed_at ?? null,
  };
}

function repairAttemptRecords(
  summary: Record<string, unknown>,
  transitions: TraceWorkflowTransition[],
): RepairAttemptView[] {
  const explicit = objectArray(summary.repair_attempts).map((value, index) => {
    const transitionRecord = asRecord(value.transition);
    const transition = parseTransition(transitionRecord);
    const attempt = firstNumber(value.attempt, transition?.attempt, index + 1) ?? index + 1;
    return {
      key: firstString(value.verdict_artifact_id, value.output_artifact_id)
        ?? "repair-" + attempt + "-" + index,
      attempt,
      target: firstString(value.target, value.repair_target) ?? targetForNode(transition?.to_node),
      status: nullableString(value.status),
      inputArtifactId: nullableString(value.input_artifact_id),
      outputArtifactId: nullableString(value.output_artifact_id),
      verdictArtifactId: nullableString(value.verdict_artifact_id),
      transition,
    };
  });
  if (explicit.length > 0) return explicit;
  return transitions.map((transition, index) => ({
    key: "transition-" + index + "-" + transition.attempt,
    attempt: firstNumber(transition.attempt, index + 1) ?? index + 1,
    target: targetForNode(transition.to_node),
    status: null,
    inputArtifactId: null,
    outputArtifactId: null,
    verdictArtifactId: null,
    transition,
  }));
}

function buildComparison(
  workflow: WorkflowTraceView,
  summary: Record<string, unknown>,
  reviewerInvocations: AgentInvocationView[],
  verdicts: VerdictView[],
): ComparisonView {
  const comparison = asRecord(summary.comparison);
  const originalRef = artifactRef(comparison.original);
  const repairedRefs = objectArray(comparison.repaired).map(artifactRef).filter(isArtifactRef);
  const finalRef = artifactRef(comparison.final_adopted);
  const attempts = objectArray(summary.repair_attempts);
  const originalId = firstString(
    originalRef?.artifact_id,
    summary.original_artifact_id,
    verdicts[0]?.reviewedArtifactIds[0],
    reviewerInvocations[0]?.input_artifact_ids[0],
  );
  const repairedIds = uniqueStrings([
    ...repairedRefs.map((item) => item.artifact_id),
    ...attempts.map((attempt) => attempt.output_artifact_id),
    summary.repaired_artifact_id,
    ...reviewerInvocations.slice(1).map((invocation) => invocation.input_artifact_ids[0]),
  ]).filter((id) => id !== originalId);
  const finalId = workflow.mode === "shadow" ? null : firstString(
    finalRef?.artifact_id,
    summary.final_artifact_id,
    latestFinalAdoptedId(workflow),
  );
  return {
    original: resolveArtifact(originalId, originalRef, workflow.artifacts),
    repaired: repairedIds.map((id) => resolveArtifact(
      id,
      repairedRefs.find((item) => item.artifact_id === id) ?? null,
      workflow.artifacts,
    )),
    finalAdopted: resolveArtifact(finalId, finalRef, workflow.artifacts),
    changed: workflow.mode === "shadow" ? null : booleanValue(comparison.changed)
      ?? (originalId && finalId ? originalId !== finalId : null),
  };
}

function buildRates(
  workflow: WorkflowTraceView,
  summary: Record<string, unknown>,
  verdicts: VerdictView[],
  metrics: TraceWorkflowReviewMetrics | null,
): RateView[] {
  const metricRates = asRecord(metrics?.rates);
  const summaryRates = asRecord(summary.rates);
  // Match the API's denominator: reviewed workflows, not individual verdicts.
  const total = hasReviewerEvidence(workflow) ? 1 : 0;
  const rejected = Number(booleanValue(summary.rejected)
    ?? verdicts.some((verdict) => verdict.verdict === "reject"));
  const repaired = Number(objectArray(summary.repair_attempts).length > 0
    || workflow.transitions.some(isReviewRepairTransition));
  const degraded = Number(booleanValue(summary.degraded)
    ?? reviewerDegradationCount(workflow) > 0);
  const hasAggregate = Object.keys(metricRates).length > 0;
  const reviewedCount = firstNumber(metrics?.counts?.reviewed_workflow_count);
  const detail = hasAggregate
    ? metricsWindowLabel(metrics) + (reviewedCount === null ? "" : " · " + reviewedCount + " 条已审查工作流")
    : "当前 Trace · " + total + " 条已审查工作流";
  return [
    {
      label: "审查拒绝率",
      rate: reviewedCount === 0 ? null : normalizedRate(firstNumber(
        metricRates.rejection_rate,
        metricRates.review_rejection_rate,
        metricRates.reviewer_rejection_rate,
        summaryRates.rejection_rate,
        summary.rejection_rate,
      )) ?? ratio(rejected, total),
      detail,
      tone: "reject",
    },
    {
      label: "修复率",
      rate: reviewedCount === 0 ? null : normalizedRate(firstNumber(
        metricRates.repair_rate,
        metricRates.review_repair_rate,
        metricRates.reviewer_repair_rate,
        summaryRates.repair_rate,
        summary.repair_rate,
      )) ?? ratio(repaired, total),
      detail,
      tone: "repair",
    },
    {
      label: "降级率",
      rate: reviewedCount === 0 ? null : normalizedRate(firstNumber(
        metricRates.degradation_rate,
        metricRates.degraded_rate,
        metricRates.review_degradation_rate,
        metricRates.reviewer_degradation_rate,
        summaryRates.degradation_rate,
        summary.degradation_rate,
      )) ?? ratio(degraded, total),
      detail,
      tone: "degraded",
    },
  ];
}

function hasReviewerEvidence(workflow: WorkflowTraceView): boolean {
  if (workflow.invocations.some((invocation) => invocation.agent_role === "response_reviewer")) {
    return true;
  }
  if (workflow.artifacts.some((artifact) =>
    ["reviewerverdict", "reviewverdict", "fidelityverdict"].includes(normalize(artifact.artifact_type)))) {
    return true;
  }
  const summary = asRecord(workflow.reviewSummary);
  return (firstNumber(summary.reviewer_invocation_count, summary.verdict_count) ?? 0) > 0
    || objectArray(summary.verdicts).length > 0;
}

function isReviewRepairTransition(transition: TraceWorkflowTransition): boolean {
  const marker = (transition.transition_type + " " + transition.reason_code).toLowerCase();
  return /repair|retry|return/.test(marker)
    && ["review_running", "review", "response_reviewer"].includes(transition.from_node);
}

function parseTransition(value: Record<string, unknown>): TraceWorkflowTransition | null {
  const fromNode = stringValue(value.from_node);
  const toNode = stringValue(value.to_node);
  if (!fromNode || !toNode) return null;
  return {
    from_node: fromNode,
    to_node: toNode,
    transition_type: stringValue(value.transition_type) ?? "repair",
    reason_code: stringValue(value.reason_code) ?? "review_repair",
    attempt: firstNumber(value.attempt, 1) ?? 1,
  };
}

function configuredRepairLimit(
  summary: Record<string, unknown>,
  workflow: WorkflowTraceView,
): number | null {
  const configured = asRecord(asRecord(summary.budget).configured_limits);
  return firstNumber(
    configured.max_upstream_repairs,
    summary.repair_budget,
    summary.max_repair_attempts,
    workflow.summary?.repair_budget,
    workflow.summary?.max_repair_attempts,
    configured.total,
    configured.max_repair_attempts,
  );
}

function budgetMessage(
  budget: Record<string, unknown>,
  exhausted: boolean,
  used: number,
  limit: number | null,
): string {
  if (exhausted) {
    const targets = stringArray(budget.exhausted_targets).map(repairTargetLabel);
    return "预算已耗尽" + (targets.length > 0 ? " · " + targets.join("、") : "");
  }
  if (limit === null) return "旧 Trace 没有保存修复上限。";
  return "剩余 " + Math.max(0, limit - used) + " 次";
}

function reviewerDegradationCount(workflow: WorkflowTraceView): number {
  const degradedInvocations = workflow.invocations.filter(
    (invocation) => invocation.agent_role === "response_reviewer"
      && invocation.status === "degraded",
  ).length;
  const degradedTransitions = workflow.transitions.filter((transition) =>
    ["review_running", "review", "response_reviewer"].includes(transition.from_node)
    && /degrad|fallback|reject|budget/.test(
      (transition.transition_type + " " + transition.reason_code).toLowerCase(),
    )).length;
  return Math.max(degradedInvocations, degradedTransitions);
}

function latestFinalAdoptedId(workflow: WorkflowTraceView): string | null {
  for (const event of [...workflow.timeline].reverse()) {
    if (event.operation !== "response_adopted") continue;
    const details = asRecord(event.details);
    if (details.final !== true) continue;
    const artifactId = stringValue(details.artifact_id);
    if (artifactId) return artifactId;
  }
  return null;
}

function resolveArtifact(
  id: string | null,
  summaryRef: TraceReviewerArtifactRef | null,
  artifacts: TraceAgentArtifact[],
): ArtifactView {
  return {
    id,
    artifact: id ? artifacts.find((artifact) => artifact.artifact_id === id) ?? null : null,
    summaryRef,
  };
}

function artifactRef(value: unknown): TraceReviewerArtifactRef | null {
  const record = asRecord(value);
  return Object.keys(record).length > 0 ? record as TraceReviewerArtifactRef : null;
}

function isArtifactRef(
  value: TraceReviewerArtifactRef | null,
): value is TraceReviewerArtifactRef {
  return value !== null;
}

function safePayloadFields(payload: Record<string, unknown> | null | undefined): string[] {
  if (!payload) return [];
  return [
    "schema_version", "verdict", "repair_target", "issue_types", "issue_count",
    "reviewed_artifact_ids", "content_blocks", "used_block_ids", "source_refs",
    "citation_refs", "style_profile_version", "communication_act",
  ].filter((key) => Object.hasOwn(payload, key)).slice(0, 10);
}

function VerdictBadge({ value }: { value: string }) {
  const labels: Record<string, string> = {
    pass: "通过",
    repair: "返回修复",
    reject: "拒绝",
    unknown: "Verdict 未记录",
  };
  const tone = ["pass", "repair", "reject"].includes(value) ? value : "unknown";
  return <span className={"review-verdict-badge review-verdict-badge-" + tone}>{labels[value] ?? value}</span>;
}

function repairTargetLabel(value: string): string {
  const labels: Record<string, string> = {
    orchestrator: "对话编排 Agent",
    nutrition_expert: "营养专业 Agent",
    response_style: "表达风格 Agent",
    unknown: "未知目标",
  };
  return labels[value] ?? value;
}

function targetForNode(value: string | undefined): string | null {
  const targets: Record<string, string> = {
    orchestrator: "orchestrator",
    orchestrator_running: "orchestrator",
    expert_running: "nutrition_expert",
    nutrition_expert: "nutrition_expert",
    nutrition_running: "nutrition_expert",
    response_style: "response_style",
    style_running: "response_style",
  };
  return value ? targets[value] ?? null : null;
}

function issueTypeLabel(value: string): string {
  const labels: Record<string, string> = {
    abusive_tone: "语气不当",
    changed_meaning: "含义改变",
    changed_uncertainty: "不确定性被改变",
    medical_overreach: "医疗越界",
    missing_user_evidence: "缺少用户证据",
    omitted_required_content: "遗漏必需内容",
    style_drift: "风格偏移",
    unsupported_claim: "Claim 缺少支持",
    unsupported_professional_claim: "专业 Claim 缺少支持",
  };
  return labels[value] ?? value;
}

function metricsWindowLabel(metrics: TraceWorkflowReviewMetrics | null): string {
  const window = asRecord(metrics?.window);
  const days = firstNumber(window.window_days, window.days);
  return days !== null ? "近 " + days + " 天聚合" : "近 7 天聚合";
}

function ratio(numerator: number, denominator: number): number | null {
  return denominator > 0 ? numerator / denominator : null;
}

function normalizedRate(value: number | null): number | null {
  if (value === null || value < 0) return null;
  return value > 1 ? Math.min(1, value / 100) : value;
}

function formatRate(value: number | null): string {
  return value === null ? "未记录" : (value * 100).toFixed(1) + "%";
}

function compareDates(left: string | null, right: string | null): number {
  const leftTime = left ? new Date(left).getTime() : 0;
  const rightTime = right ? new Date(right).getTime() : 0;
  return (Number.isFinite(leftTime) ? leftTime : 0) - (Number.isFinite(rightTime) ? rightTime : 0);
}

function normalize(value: string): string {
  return value.toLowerCase().replaceAll(/[^a-z0-9]/g, "");
}

function uniqueStrings(values: unknown[]): string[] {
  return [...new Set(values.filter(
    (value): value is string => typeof value === "string" && value.length > 0,
  ))];
}

function objectArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> =>
      item !== null && typeof item === "object" && !Array.isArray(item))
    : [];
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string" && item.length > 0)
    : [];
}

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function firstString(...values: unknown[]): string | null {
  for (const value of values) {
    const parsed = stringValue(value);
    if (parsed) return parsed;
  }
  return null;
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function nullableString(value: unknown): string | null {
  return value == null ? null : stringValue(value);
}

function firstNumber(...values: unknown[]): number | null {
  for (const value of values) {
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return null;
}

function booleanValue(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}
