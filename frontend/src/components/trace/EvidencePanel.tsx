import type {
  TraceAgentArtifact,
  TraceNutritionCalculation,
  TraceNutritionKnowledgeStatus,
} from "../../types";
import type { WorkflowTraceView } from "./model";
import { claimAnchorId, RagCitationPanel } from "./RagCitationPanel";

interface EvidenceViewItem {
  id: string;
  sourceType: string;
  group: string;
  authority: string;
  confidence: string | null;
  occurredAt: string | null;
  fieldNames: string[];
  hasUncertainty: boolean;
  calculationValue: string | null;
  content: Record<string, unknown> | null;
}

interface ClaimView {
  claimId: string;
  category: string;
  statement: string | null;
  basisTypes: string[];
  evidenceRefs: string[];
  knowledgeRefs: string[];
  confidence: string;
}

interface ActionView {
  actionId: string;
  statement: string | null;
  basisClaimIds: string[];
}

interface EvidenceGroup {
  key: string;
  title: string;
  description: string;
  items: EvidenceViewItem[];
}

export function EvidencePanel({ workflow }: { workflow: WorkflowTraceView }) {
  const nutritionInvocations = workflow.invocations.filter(
    (invocation) => invocation.agent_role === "nutrition_expert",
  );
  if (nutritionInvocations.length === 0) return null;

  const evidenceArtifact = latestArtifact(workflow.artifacts, "evidencepacket");
  const observationsArtifact = latestArtifact(workflow.artifacts, "nutritionobservations");
  const assessmentArtifact = latestArtifact(workflow.artifacts, "professionalassessment");
  const evidenceItems = collectEvidence(evidenceArtifact, observationsArtifact);
  const groups = groupEvidence(evidenceItems);
  const assessment = artifactPayload(assessmentArtifact);
  const knowledge = knowledgeStatus(observationsArtifact);
  const findings = professionalClaims(assessment.findings);
  const actions = professionalActions(assessment.actions);

  return (
    <section className="evidence-panel">
      <div className="section-heading evidence-heading">
        <div>
          <h2>营养分析的证据与结论</h2>
          <p>只展示实际 Nutrition Invocation 使用的来源、权威等级和引用关系。</p>
        </div>
        <div className="evidence-heading-meta">
          <span>{nutritionInvocations.length} 次 Nutrition 调用</span>
          <span>{evidenceItems.length} 条证据</span>
          <span>{findings.length} 条 Claim</span>
        </div>
      </div>
      <div className="evidence-privacy-note">
        <strong>最小化展示</strong>
        <span>证据正文和计算输入默认隐藏；这里仅显示来源类型、引用 ID、字段类别与可信程度。</span>
      </div>
      {groups.length > 0 ? (
        <div className="evidence-groups">
          {groups.map((group) => <EvidenceSourceGroup group={group} key={group.key} />)}
        </div>
      ) : (
        <div className="evidence-empty">该 Nutrition Invocation 没有可展示的 Evidence Packet。</div>
      )}
      <KnowledgeState knowledge={knowledge} assessment={assessment} />
      <RagCitationPanel
        assessment={assessment}
        claims={findings.map((claim) => ({
          claimId: claim.claimId,
          knowledgeRefs: claim.knowledgeRefs,
        }))}
        knowledge={knowledge}
      />
      <ClaimPanel
        assessmentArtifact={assessmentArtifact}
        assessment={assessment}
        findings={findings}
        evidenceItems={evidenceItems}
        actions={actions}
        knowledge={knowledge}
      />
    </section>
  );
}

function EvidenceSourceGroup({ group }: { group: EvidenceGroup }) {
  return (
    <article className={`evidence-group evidence-group-${group.key}`}>
      <header><div><h3>{group.title}</h3><p>{group.description}</p></div><span>{group.items.length}</span></header>
      <div>
        {group.items.map((item) => (
          <article className="evidence-item" key={item.id}>
            <header>
              <strong>{sourceTypeLabel(item.sourceType)}</strong>
              <div>
                <EvidenceBadge value={item.authority} kind="authority" />
                <EvidenceBadge value={item.confidence} kind="confidence" />
              </div>
            </header>
            {item.calculationValue && <p className="calculation-value">{item.calculationValue}</p>}
            <dl>
              <div><dt>Evidence ID</dt><dd>{item.id}</dd></div>
              <div><dt>发生时间</dt><dd>{formatDate(item.occurredAt)}</dd></div>
              <div><dt>内容字段</dt><dd>{item.fieldNames.length > 0 ? item.fieldNames.join("、") : "正文已隐藏"}</dd></div>
            </dl>
            {item.hasUncertainty && <small className="uncertainty-flag">包含不确定性说明，未提升为确定事实</small>}
            {item.content && Object.keys(item.content).length > 0 && (
              <details className="evidence-content">
                <summary>查看证据正文（默认收起）</summary>
                <p>此区域可能包含敏感健康信息。</p>
                <pre>{formatEvidenceContent(item.content)}</pre>
              </details>
            )}
          </article>
        ))}
      </div>
    </article>
  );
}

function KnowledgeState({
  knowledge,
  assessment,
}: {
  knowledge: TraceNutritionKnowledgeStatus | null;
  assessment: Record<string, unknown>;
}) {
  const citationCount = Array.isArray(knowledge?.citations)
    ? knowledge.citations.length
    : objectArray(assessment.citations).length;
  if (!knowledge) {
    return (
      <div className="knowledge-state knowledge-not-run">
        <strong>专业知识库</strong><span>本轮没有知识检索记录；这不代表知识库为空。</span>
      </div>
    );
  }
  const views: Record<string, { title: string; detail: string }> = {
    empty: { title: "专业知识库为空", detail: "本轮没有可检索资料，也没有生成伪造的文章引用。" },
    available: { title: "专业知识库可用", detail: `本轮实际返回 ${citationCount} 条引用元数据。` },
    unavailable: { title: "专业知识库不可用", detail: "本轮不能依赖知识库资料形成结论。" },
    error: { title: "专业知识检索失败", detail: "检索故障已记录，本轮不能把未返回资料当作依据。" },
  };
  const view = views[knowledge.corpus_status] ?? {
    title: `知识库状态：${knowledge.corpus_status}`,
    detail: "这是后端记录的原始检索状态。",
  };
  return (
    <div className={`knowledge-state knowledge-${knowledge.corpus_status}`}>
      <strong>{view.title}</strong><span>{view.detail}</span>
    </div>
  );
}

function ClaimPanel({
  assessmentArtifact,
  assessment,
  findings,
  evidenceItems,
  actions,
  knowledge,
}: {
  assessmentArtifact: TraceAgentArtifact | null;
  assessment: Record<string, unknown>;
  findings: ClaimView[];
  evidenceItems: EvidenceViewItem[];
  actions: ActionView[];
  knowledge: TraceNutritionKnowledgeStatus | null;
}) {
  const knownEvidence = new Set(evidenceItems.map((item) => item.id));
  const knownKnowledge = new Set(
    [...objectArray(assessment.citations), ...objectArray(knowledge?.citations)].flatMap((citation) =>
      [stringValue(citation.citation_id), stringValue(citation.chunk_id)].filter(
        (value): value is string => value !== null,
      ),
    ),
  );
  return (
    <div className="claim-panel">
      <header>
        <div>
          <h3>Professional Claims</h3>
          <p>{stringValue(assessment.assessment_type) ? `分析类型：${stringValue(assessment.assessment_type)}` : "逐条核对结论所引用的证据与知识来源。"}</p>
        </div>
        <code>{assessmentArtifact?.artifact_id ?? "无 ProfessionalAssessment Artifact"}</code>
      </header>
      {findings.length > 0 ? (
        <div className="claim-list">
          {findings.map((claim) => {
            const missingEvidence = claim.evidenceRefs.filter((reference) => !knownEvidence.has(reference));
            const missingKnowledge = claim.knowledgeRefs.filter((reference) => !knownKnowledge.has(reference));
            return (
              <article
                className={`claim-card confidence-${claim.confidence}`}
                id={claimAnchorId(claim.claimId)}
                key={claim.claimId}
              >
                <header>
                  <div><span>{claim.category}</span><strong>{claim.statement ?? "Claim 正文已隐藏"}</strong></div>
                  <EvidenceBadge value={claim.confidence} kind="confidence" />
                </header>
                <ReferenceRow label="依据类型" values={claim.basisTypes} labels={BASIS_LABELS} />
                <ReferenceRow label="Evidence" values={claim.evidenceRefs} />
                <ReferenceRow label="Knowledge" values={claim.knowledgeRefs} empty="未使用知识资料" />
                {missingEvidence.length > 0 && (
                  <div className="claim-reference-error">当前展示包缺少 Evidence：{missingEvidence.join("、")}</div>
                )}
                {knowledge?.corpus_status === "empty" && claim.knowledgeRefs.length > 0 && (
                  <div className="claim-reference-error">知识库为空，但该 Claim 声明了知识引用；不能按正常引用展示。</div>
                )}
                {knowledge?.corpus_status !== "empty" && missingKnowledge.length > 0 && (
                  <div className="claim-reference-error">当前 Assessment 缺少 Knowledge 引用：{missingKnowledge.join("、")}</div>
                )}
                <small>Claim ID · {claim.claimId}</small>
              </article>
            );
          })}
        </div>
      ) : (
        <div className="evidence-empty">本轮没有形成 Professional Claim。</div>
      )}
      {actions.length > 0 && (
        <div className="action-claim-map">
          <h3>Action → Claim</h3>
          {actions.map((action) => (
            <article key={action.actionId}>
              <div><span>行动</span><strong>{action.statement ?? "Action 正文已隐藏"}</strong><code>{action.actionId}</code></div>
              <span className="action-arrow">←</span>
              <div><span>依据 Claim</span><div>{action.basisClaimIds.length > 0 ? action.basisClaimIds.map((id) => <code key={id}>{id}</code>) : <small>无 Claim 引用</small>}</div></div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}

const BASIS_LABELS: Record<string, string> = {
  user_evidence: "用户证据",
  visual_observation: "图片观察",
  deterministic_calculation: "确定性计算",
  model_prior: "模型一般知识",
  rag_evidence: "专业知识库",
};

function ReferenceRow({
  label,
  values,
  labels = {},
  empty = "无",
}: {
  label: string;
  values: string[];
  labels?: Record<string, string>;
  empty?: string;
}) {
  return (
    <div className="claim-references">
      <span>{label}</span>
      <div>{values.length > 0 ? values.map((value) => <code key={value}>{labels[value] ?? value}</code>) : <small>{empty}</small>}</div>
    </div>
  );
}

function collectEvidence(
  evidenceArtifact: TraceAgentArtifact | null,
  observationsArtifact: TraceAgentArtifact | null,
): EvidenceViewItem[] {
  const packet = artifactPayload(evidenceArtifact);
  const observations = artifactPayload(observationsArtifact);
  const rawItems = [
    ...objectArray(packet.items),
    ...objectArray(packet.evidence),
    ...objectArray(observations.evidence),
    ...objectArray(observations.items),
  ];
  const items = rawItems.map(evidenceView).filter((item): item is EvidenceViewItem => item !== null);
  const calculations = [
    ...objectArray(observations.calculations),
    ...objectArray(observations.calculation_results),
    ...objectArray(observations.calculation_observations),
  ].map(calculationView).filter((item): item is EvidenceViewItem => item !== null);
  const unique = new Map<string, EvidenceViewItem>();
  for (const item of [...items, ...calculations]) unique.set(item.id, item);
  return [...unique.values()];
}

function evidenceView(raw: Record<string, unknown>): EvidenceViewItem | null {
  const id = stringValue(raw.evidence_id) ?? stringValue(raw.observation_id);
  const sourceType = stringValue(raw.source_type);
  if (!id || !sourceType) return null;
  const content = asRecord(raw.content);
  return {
    id,
    sourceType,
    group: evidenceGroup(sourceType),
    authority: stringValue(raw.authority) ?? "unknown",
    confidence: nullableString(raw.confidence),
    occurredAt: nullableString(raw.occurred_at),
    fieldNames: visibleFieldNames(content),
    hasUncertainty: Boolean(stringValue(raw.uncertainty)),
    calculationValue: null,
    content: Object.keys(content).length > 0 ? content : null,
  };
}

function calculationView(raw: Record<string, unknown>): EvidenceViewItem | null {
  const calculation = raw as Partial<TraceNutritionCalculation>;
  if (typeof calculation.observation_id !== "string" || !calculation.observation_id) return null;
  const value = typeof calculation.value === "number" || typeof calculation.value === "string"
    ? String(calculation.value)
    : "结果已记录";
  return {
    id: calculation.observation_id,
    sourceType: calculation.calculation_type ?? "deterministic_calculation",
    group: "calculation",
    authority: "deterministic",
    confidence: "deterministic",
    occurredAt: null,
    fieldNames: visibleFieldNames(asRecord(calculation.inputs)),
    hasUncertainty: false,
    calculationValue: `${value}${calculation.unit ? ` ${calculation.unit}` : ""}`,
    content: null,
  };
}

function professionalClaims(value: unknown): ClaimView[] {
  return objectArray(value).flatMap((raw) => {
    const claimId = stringValue(raw.claim_id);
    if (!claimId) return [];
    return [{
      claimId,
      category: stringValue(raw.category) ?? "uncategorized",
      statement: nullableString(raw.statement),
      basisTypes: stringArray(raw.basis_types),
      evidenceRefs: stringArray(raw.evidence_refs),
      knowledgeRefs: stringArray(raw.knowledge_refs),
      confidence: stringValue(raw.confidence) ?? "unknown",
    }];
  });
}

function professionalActions(value: unknown): ActionView[] {
  return objectArray(value).flatMap((raw) => {
    const actionId = stringValue(raw.action_id);
    if (!actionId) return [];
    return [{
      actionId,
      statement: nullableString(raw.statement),
      basisClaimIds: stringArray(raw.basis_claim_ids),
    }];
  });
}

function groupEvidence(items: EvidenceViewItem[]): EvidenceGroup[] {
  const definitions: Array<Omit<EvidenceGroup, "items">> = [
    { key: "user", title: "用户原话", description: "用户在本轮明确表达的信息，不等同于医学确认。" },
    { key: "working", title: "Working Memory", description: "有限对话窗口，只用于理解当前上下文。" },
    { key: "durable", title: "长期记忆", description: "用户明确表达并通过记忆治理保存的资料。" },
    { key: "database", title: "数据库权威记录", description: "体重、体脂、饮食、运动和工具回执等业务事实。" },
    { key: "vision", title: "图片观察", description: "视觉模型的有限观察，不能自动提升为确定事实。" },
    { key: "calculation", title: "确定性计算", description: "由固定规则根据已引用输入计算，不是模型猜测。" },
    { key: "other", title: "其他证据", description: "后端保留的其他可追踪证据类型。" },
  ];
  return definitions
    .map((definition) => ({ ...definition, items: items.filter((item) => item.group === definition.key) }))
    .filter((group) => group.items.length > 0);
}

function evidenceGroup(sourceType: string): string {
  if (["user_message", "user_input", "user_report"].includes(sourceType)) return "user";
  if (["working_memory", "recent_dialogue", "conversation_context"].includes(sourceType)) return "working";
  if (["profile_memory", "goal_memory", "constraint_memory", "durable_memory"].includes(sourceType)) return "durable";
  if (["vision_observation", "image_observation"].includes(sourceType)) return "vision";
  if (["deterministic_calculation", "calculation"].includes(sourceType)) return "calculation";
  if (["weight_record", "body_fat_record", "meal_record", "exercise_record", "tool_receipt", "database_record"].includes(sourceType)) return "database";
  return "other";
}

function knowledgeStatus(artifact: TraceAgentArtifact | null): TraceNutritionKnowledgeStatus | null {
  const payload = artifactPayload(artifact);
  const nested = asRecord(payload.knowledge);
  const fallback = asRecord(payload.knowledge_status);
  const source = stringValue(nested.corpus_status)
    ? nested
    : stringValue(fallback.corpus_status)
      ? fallback
      : payload;
  const corpusStatus = stringValue(source.corpus_status);
  if (!corpusStatus) return null;
  return {
    corpus_status: corpusStatus,
    citations: Array.isArray(source.citations) ? source.citations : [],
    ...(Array.isArray(source.candidates) ? { candidates: source.candidates } : {}),
    ...(Array.isArray(source.candidate_citations) ? { candidate_citations: source.candidate_citations } : {}),
    ...(Array.isArray(source.retrieved_candidates) ? { retrieved_candidates: source.retrieved_candidates } : {}),
    ...(Array.isArray(source.adopted_citations) ? { adopted_citations: source.adopted_citations } : {}),
    ...(Array.isArray(source.final_citations) ? { final_citations: source.final_citations } : {}),
    query_summary: nullableString(source.query_summary),
  };
}

function EvidenceBadge({
  value,
  kind,
}: {
  value: string | null;
  kind: "authority" | "confidence";
}) {
  const label = kind === "authority" ? authorityLabel(value) : confidenceLabel(value);
  return <span className={`evidence-badge evidence-badge-${badgeTone(value)}`}>{label}</span>;
}

function authorityLabel(value: string | null): string {
  const labels: Record<string, string> = {
    authoritative: "权威记录",
    user_reported: "用户自述",
    observation: "有限观察",
    deterministic: "确定性计算",
    unknown: "权威性未标注",
  };
  return value ? labels[value] ?? value : "权威性未标注";
}

function confidenceLabel(value: string | null): string {
  const labels: Record<string, string> = {
    high: "高置信度",
    medium: "中置信度",
    low: "低置信度",
    deterministic: "规则确定",
  };
  return value ? labels[value] ?? value : "置信度未标注";
}

function badgeTone(value: string | null): string {
  if (["authoritative", "high", "deterministic"].includes(value ?? "")) return "strong";
  if (["user_reported", "medium"].includes(value ?? "")) return "reported";
  if (["observation", "low"].includes(value ?? "")) return "observed";
  return "neutral";
}

function sourceTypeLabel(value: string): string {
  const labels: Record<string, string> = {
    user_message: "用户消息",
    working_memory: "近期对话",
    profile_memory: "用户档案记忆",
    goal_memory: "目标记忆",
    constraint_memory: "限制条件记忆",
    weight_record: "体重记录",
    body_fat_record: "体脂记录",
    meal_record: "饮食记录",
    exercise_record: "运动记录",
    tool_receipt: "工具执行回执",
    vision_observation: "图片识别观察",
    deterministic_calculation: "确定性计算",
  };
  return labels[value] ?? value;
}

function visibleFieldNames(content: Record<string, unknown>): string[] {
  const hidden = new Set(["text", "content", "statement", "message", "raw", "prompt"]);
  return Object.keys(content).filter((key) => !hidden.has(key.toLowerCase())).slice(0, 8);
}

function formatEvidenceContent(content: Record<string, unknown>): string {
  return JSON.stringify(sanitizeEvidenceValue(content), null, 2);
}

function sanitizeEvidenceValue(value: unknown, depth = 0): unknown {
  if (depth > 4) return "[内容层级过深]";
  if (Array.isArray(value)) {
    const items = value.slice(0, 20).map((item) => sanitizeEvidenceValue(item, depth + 1));
    return value.length > 20 ? [...items, `[另有 ${value.length - 20} 项]`] : items;
  }
  if (value !== null && typeof value === "object") {
    const hidden = new Set([
      "chain_of_thought",
      "credentials",
      "messages",
      "prompt",
      "raw_model_response",
      "reasoning",
      "system_prompt",
      "token",
      "tool_calls",
    ]);
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .filter(([key]) => !hidden.has(key.toLowerCase()))
        .slice(0, 30)
        .map(([key, item]) => [key, sanitizeEvidenceValue(item, depth + 1)]),
    );
  }
  if (typeof value === "string" && value.length > 1_000) return `${value.slice(0, 1_000)}…`;
  return value;
}

function latestArtifact(
  artifacts: TraceAgentArtifact[],
  normalizedType: string,
): TraceAgentArtifact | null {
  return [...artifacts].reverse().find((artifact) =>
    artifact.artifact_type.toLowerCase().replaceAll(/[^a-z0-9]/g, "") === normalizedType,
  ) ?? null;
}

function artifactPayload(artifact: TraceAgentArtifact | null): Record<string, unknown> {
  return asRecord(artifact?.payload);
}

function objectArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => item !== null && typeof item === "object" && !Array.isArray(item))
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

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function nullableString(value: unknown): string | null {
  return value == null ? null : stringValue(value);
}

function formatDate(value: string | null): string {
  if (!value) return "未记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}
