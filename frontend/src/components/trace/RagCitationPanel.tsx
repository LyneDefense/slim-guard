import type {
  TraceKnowledgeCitation,
  TraceNutritionKnowledgeStatus,
} from "../../types";

interface ClaimCitationReference {
  claimId: string;
  knowledgeRefs: string[];
}

interface CitationView {
  key: string;
  rank: number | null;
  citationId: string | null;
  sourceId: string | null;
  chunkId: string | null;
  title: string | null;
  publisher: string | null;
  publishedAt: string | null;
  version: string | null;
  sectionOrPage: string | null;
  sourceUrl: string | null;
  applicability: string[];
  reviewStatus: string;
  retrievedInInvocationId: string | null;
  keywordScore: number | null;
  vectorScore: number | null;
  rerankScore: number | null;
  matchReasons: string[];
  adoptionStatus: string | null;
  sensitiveBody: string | null;
}

export function RagCitationPanel({
  knowledge,
  assessment,
  claims,
}: {
  knowledge: TraceNutritionKnowledgeStatus | null;
  assessment: Record<string, unknown>;
  claims: ClaimCitationReference[];
}) {
  const candidates = citationViews(candidateCitations(knowledge));
  const adopted = citationViews(adoptedCitations(knowledge, assessment));
  const knownRefs = new Set(adopted.flatMap((citation) => citationReferences(citation)));
  const brokenRefs = [...new Set(
    claims.flatMap((claim) => claim.knowledgeRefs).filter((reference) => !knownRefs.has(reference)),
  )];

  return (
    <section className="rag-panel" aria-labelledby="rag-panel-heading">
      <header>
        <div>
          <h3 id="rag-panel-heading">RAG Citation 采用链路</h3>
          <p>左侧是实际检索候选，右侧是最终进入 ProfessionalAssessment 的引用。</p>
        </div>
        <div className="rag-counts">
          <span>{candidates.length} 个候选</span>
          <span>{adopted.length} 个采用</span>
        </div>
      </header>

      {brokenRefs.length > 0 && (
        <div className="rag-broken-reference" role="status">
          <strong>存在断链引用</strong>
          <span>Claim 指向了未进入最终采用栏的 Citation：{brokenRefs.join("、")}</span>
        </div>
      )}

      <div className="rag-columns">
        <CitationColumn
          citations={candidates}
          claims={claims}
          emptyMessage={candidateEmptyMessage(knowledge)}
          title="检索候选"
          tone="candidate"
        />
        <CitationColumn
          citations={adopted}
          claims={claims}
          emptyMessage={adoptedEmptyMessage(knowledge, candidates)}
          title="最终采用"
          tone="adopted"
        />
      </div>
    </section>
  );
}

export function claimAnchorId(claimId: string): string {
  return `professional-claim-${claimId.replaceAll(/[^a-zA-Z0-9_-]/g, "-")}`;
}

function CitationColumn({
  title,
  citations,
  claims,
  emptyMessage,
  tone,
}: {
  title: string;
  citations: CitationView[];
  claims: ClaimCitationReference[];
  emptyMessage: string;
  tone: "candidate" | "adopted";
}) {
  return (
    <section className={`rag-column rag-column-${tone}`}>
      <header><h4>{title}</h4><span>{citations.length}</span></header>
      {citations.length > 0 ? (
        <div className="citation-list">
          {citations.map((citation) => (
            <CitationCard citation={citation} claims={claims} key={citation.key} tone={tone} />
          ))}
        </div>
      ) : <div className="citation-empty">{emptyMessage}</div>}
    </section>
  );
}

function CitationCard({
  citation,
  claims,
  tone,
}: {
  citation: CitationView;
  claims: ClaimCitationReference[];
  tone: "candidate" | "adopted";
}) {
  const supportedClaims = claims.filter((claim) =>
    claim.knowledgeRefs.some((reference) => citationReferences(citation).includes(reference)),
  );
  const link = validHttpUrl(citation.sourceUrl);
  const retired = citation.reviewStatus === "retired";
  return (
    <article className={`citation-card citation-${tone}${retired ? " citation-retired" : ""}`}>
      <header>
        <div>
          <span>{citation.rank !== null ? `#${citation.rank} · ` : ""}{citation.citationId ?? citation.chunkId ?? "Citation ID 未记录"}</span>
          <h5>{citation.title ?? "标题未记录（旧 API）"}</h5>
        </div>
        <ReviewBadge status={citation.reviewStatus} />
      </header>

      {retired && (
        <div className="citation-warning">该资料已 retired，不应作为新的最终依据。</div>
      )}

      <dl>
        <div><dt>机构</dt><dd>{citation.publisher ?? "未记录"}</dd></div>
        <div><dt>版本</dt><dd>{citation.version ?? "未记录"}</dd></div>
        <div><dt>章节 / 页码</dt><dd>{citation.sectionOrPage ?? "未记录"}</dd></div>
        <div><dt>发布日期</dt><dd>{citation.publishedAt ?? "未记录"}</dd></div>
      </dl>

      <div className="citation-applicability">
        <span>适用范围</span>
        <div>{citation.applicability.length > 0
          ? citation.applicability.map((item) => <code key={item}>{item}</code>)
          : <small>未记录适用范围</small>}
        </div>
      </div>

      {(citation.adoptionStatus || citation.matchReasons.length > 0 || hasScores(citation)) && (
        <details className="citation-match-details">
          <summary>检索匹配详情</summary>
          {citation.adoptionStatus && <p>采用状态 · {citation.adoptionStatus}</p>}
          {citation.matchReasons.length > 0 && <p>匹配原因 · {citation.matchReasons.join("、")}</p>}
          {hasScores(citation) && (
            <p>keyword {formatScore(citation.keywordScore)} · vector {formatScore(citation.vectorScore)} · rerank {formatScore(citation.rerankScore)}</p>
          )}
        </details>
      )}

      <div className="citation-claims">
        <span>支撑 Claim</span>
        <div>{supportedClaims.length > 0
          ? supportedClaims.map((claim) => (
            <a href={`#${claimAnchorId(claim.claimId)}`} key={claim.claimId}>{claim.claimId}</a>
          ))
          : <small>{tone === "candidate" ? "未被 Claim 采用" : "没有 Claim 指向该 Citation"}</small>}
        </div>
      </div>

      <div className="citation-link">
        {link
          ? <a href={link} rel="noreferrer" target="_blank">打开来源 ↗</a>
          : <span>来源链接缺失或无效，无法外部核验</span>}
      </div>

      {citation.sensitiveBody && (
        <details className="citation-body">
          <summary>查看检索片段（敏感内容，默认收起）</summary>
          <p>{citation.sensitiveBody}</p>
        </details>
      )}

      <footer>
        <code>source · {citation.sourceId ?? "未记录"}</code>
        <code>chunk · {citation.chunkId ?? "未记录"}</code>
        {citation.retrievedInInvocationId && <code>invocation · {citation.retrievedInInvocationId}</code>}
      </footer>
    </article>
  );
}

function ReviewBadge({ status }: { status: string }) {
  const labels: Record<string, string> = {
    approved: "审核通过",
    draft: "草稿",
    retired: "已退役",
    unknown: "审核状态未记录",
  };
  return <span className={`citation-review citation-review-${status}`}>{labels[status] ?? status}</span>;
}

function candidateCitations(
  knowledge: TraceNutritionKnowledgeStatus | null,
): TraceKnowledgeCitation[] {
  if (!knowledge) return [];
  const hasExplicitCandidates = Array.isArray(knowledge.candidates)
    || Array.isArray(knowledge.candidate_citations)
    || Array.isArray(knowledge.retrieved_candidates);
  const explicitCandidates = [
    ...citationArray(knowledge.candidates),
    ...citationArray(knowledge.candidate_citations),
    ...citationArray(knowledge.retrieved_candidates),
  ];
  return uniqueCitations(
    hasExplicitCandidates ? explicitCandidates : citationArray(knowledge.citations),
  );
}

function adoptedCitations(
  knowledge: TraceNutritionKnowledgeStatus | null,
  assessment: Record<string, unknown>,
): TraceKnowledgeCitation[] {
  return uniqueCitations([
    ...citationArray(knowledge?.adopted_citations),
    ...citationArray(knowledge?.final_citations),
    ...citationArray(assessment.citations),
  ]);
}

function uniqueCitations(citations: TraceKnowledgeCitation[]): TraceKnowledgeCitation[] {
  const unique = new Map<string, TraceKnowledgeCitation>();
  for (const [index, citation] of citations.entries()) {
    const key = stringValue(citation.citation_id)
      ?? stringValue(citation.chunk_id)
      ?? stringValue(citation.source_id)
      ?? `citation-${index}`;
    if (!unique.has(key)) unique.set(key, citation);
  }
  return [...unique.values()];
}

function citationViews(citations: TraceKnowledgeCitation[]): CitationView[] {
  return citations.map((citation, index) => ({
    key: stringValue(citation.citation_id)
      ?? stringValue(citation.chunk_id)
      ?? stringValue(citation.source_id)
      ?? `citation-${index}`,
    rank: numberValue(citation.rank),
    citationId: nullableString(citation.citation_id),
    sourceId: nullableString(citation.source_id),
    chunkId: nullableString(citation.chunk_id),
    title: nullableString(citation.title),
    publisher: nullableString(citation.publisher),
    publishedAt: nullableString(citation.published_at),
    version: nullableString(citation.version),
    sectionOrPage: nullableString(citation.section_or_page),
    sourceUrl: nullableString(citation.source_url),
    applicability: stringArray(citation.applicability),
    reviewStatus: stringValue(citation.review_status) ?? "unknown",
    retrievedInInvocationId: nullableString(citation.retrieved_in_invocation_id),
    keywordScore: numberValue(citation.keyword_score),
    vectorScore: numberValue(citation.vector_score),
    rerankScore: numberValue(citation.rerank_score),
    matchReasons: stringArray(citation.match_reasons),
    adoptionStatus: nullableString(citation.adoption_status),
    sensitiveBody: firstString(citation.excerpt, citation.snippet, citation.content),
  }));
}

function citationReferences(citation: CitationView): string[] {
  return [citation.citationId, citation.sourceId, citation.chunkId].filter(
    (value): value is string => value !== null,
  );
}

function hasScores(citation: CitationView): boolean {
  return [citation.keywordScore, citation.vectorScore, citation.rerankScore]
    .some((score) => score !== null);
}

function formatScore(value: number | null): string {
  return value === null ? "—" : value.toFixed(3);
}

function candidateEmptyMessage(knowledge: TraceNutritionKnowledgeStatus | null): string {
  if (!knowledge) return "旧 Trace 未记录 RAG 检索，不能推断候选内容。";
  if (knowledge.corpus_status === "empty") return "知识库为空，本轮没有候选，也不会显示伪造资料。";
  if (knowledge.corpus_status === "unavailable") return "知识库不可用，本轮没有检索候选。";
  if (knowledge.corpus_status === "error") return "检索失败，本轮没有可靠候选。";
  if (knowledge.corpus_status === "retired") return "知识库版本已 retired，本轮没有可用候选。";
  return "本轮没有记录检索候选。";
}

function adoptedEmptyMessage(
  knowledge: TraceNutritionKnowledgeStatus | null,
  candidates: CitationView[],
): string {
  if (!knowledge) return "旧 Trace 未记录最终 Citation 采用结果。";
  if (knowledge.corpus_status === "empty") return "知识库为空，本轮没有最终采用资料。";
  if (["unavailable", "error", "retired"].includes(knowledge.corpus_status)) {
    return "当前知识库状态不允许采用 Citation。";
  }
  return candidates.length > 0 ? "候选均未被最终采用。" : "本轮没有采用 Citation。";
}

function citationArray(value: unknown): TraceKnowledgeCitation[] {
  return Array.isArray(value)
    ? value.filter((item): item is TraceKnowledgeCitation =>
      item !== null && typeof item === "object" && !Array.isArray(item))
    : [];
}

function validHttpUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.toString() : null;
  } catch {
    return null;
  }
}

function firstString(...values: unknown[]): string | null {
  for (const value of values) {
    const parsed = stringValue(value);
    if (parsed) return parsed.length > 2_000 ? `${parsed.slice(0, 2_000)}…` : parsed;
  }
  return null;
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string" && item.length > 0)
    : [];
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function nullableString(value: unknown): string | null {
  return value == null ? null : stringValue(value);
}
