import { type FormEvent, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";

import { api } from "../../api";
import type {
  NutritionEvaluationCase,
  NutritionEvaluationRun,
  NutritionJob,
  NutritionRelease,
  NutritionReviewDecision,
  NutritionReviewType,
  NutritionSourceDetail,
  NutritionSourceSummary,
} from "../../types";

const TABS = [
  ["dashboard", "总览"],
  ["sources", "资料源"],
  ["jobs", "后台任务"],
  ["lab", "检索实验室"],
  ["releases", "语料版本"],
  ["evaluation", "评测集"],
] as const;
type TabKey = (typeof TABS)[number][0];

const STATUS_LABELS: Record<string, string> = {
  draft: "草稿",
  approved: "已审核",
  published: "已发布",
  active: "使用中",
  retired: "已退役",
  rejected: "已拒绝",
  queued: "排队中",
  running: "处理中",
  retry_wait: "等待重试",
  succeeded: "成功",
  failed: "失败",
  cancelled: "已取消",
  evaluating: "待评测",
  review_ready: "待人工验收",
  indexing_check: "索引检查",
  ready: "可评测",
  insufficient: "证据不足",
};

const REVIEW_LABELS: Record<NutritionReviewType, string> = {
  content: "内容准确性",
  applicability: "适用范围",
  rights: "版权与使用权",
};

function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "操作失败，请刷新后重试。";
}

function splitLabels(value: string): string[] {
  return Array.from(new Set(value.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean)));
}

function Status({ value }: { value: string | null | undefined }) {
  const status = value || "unknown";
  const tone = ["approved", "published", "active", "succeeded", "ready"].includes(status)
    ? "good"
    : ["failed", "rejected", "cancelled"].includes(status)
      ? "bad"
      : ["queued", "running", "retry_wait", "evaluating", "review_ready"].includes(status)
        ? "warn"
        : "neutral";
  return <span className={`status status-${tone}`}>{STATUS_LABELS[status] ?? status}</span>;
}

function Loading({ label = "正在读取数据" }: { label?: string }) {
  return <div className="state-card"><span className="spinner" />{label}</div>;
}

function Failure({ error }: { error: unknown }) {
  return <div className="state-card state-error">{errorText(error)}</div>;
}

function Technical({ value, label = "查看技术详情" }: { value: unknown; label?: string }) {
  return <details className="json-view"><summary>{label}</summary><pre>{JSON.stringify(value, null, 2)}</pre></details>;
}

export function NutritionKnowledgePage() {
  const [params, setParams] = useSearchParams();
  const requested = params.get("tab") as TabKey | null;
  const tab = TABS.some(([key]) => key === requested) ? requested as TabKey : "dashboard";

  return (
    <div className="page nutrition-page">
      <header className="page-header">
        <div>
          <p className="eyebrow">NUTRITION KNOWLEDGE · HYBRID RAG</p>
          <h1>营养知识库</h1>
          <p>管理原始资料、混合检索索引、质量评测和 Agent 当前使用的语料版本。</p>
        </div>
      </header>
      <nav className="nutrition-tabs" aria-label="营养知识库工作区">
        {TABS.map(([key, label]) => (
          <button
            type="button"
            className={tab === key ? "active" : ""}
            onClick={() => setParams(key === "dashboard" ? {} : { tab: key })}
            key={key}
          >{label}</button>
        ))}
      </nav>
      {tab === "dashboard" && <Dashboard />}
      {tab === "sources" && <Sources />}
      {tab === "jobs" && <Jobs />}
      {tab === "lab" && <RetrievalLab />}
      {tab === "releases" && <Releases />}
      {tab === "evaluation" && <Evaluation />}
    </div>
  );
}

function Dashboard() {
  const query = useQuery({
    queryKey: ["nutrition-dashboard"],
    queryFn: api.nutritionDashboard,
    refetchInterval: 10_000,
  });
  if (query.isLoading) return <Loading />;
  if (query.error || !query.data) return <Failure error={query.error} />;
  const data = query.data;
  const sourceCount = Object.values(data.sources).reduce((sum, count) => sum + count, 0);
  const activeJobs = (data.jobs.queued ?? 0) + (data.jobs.running ?? 0) + (data.jobs.retry_wait ?? 0);
  return (
    <>
      {(!data.capabilities.cos_configured || !data.capabilities.worker_enabled) && (
        <section className="nutrition-warning">
          <strong>运行配置未完成</strong>
          <span>
            {!data.capabilities.cos_configured && "腾讯云 COS 尚未配置；"}
            {!data.capabilities.worker_enabled && "知识处理 Worker 尚未启用；"}
            上传和索引功能暂不可用。
          </span>
        </section>
      )}
      <section className="nutrition-metrics">
        <article><strong>{sourceCount}</strong><span>资料版本</span></article>
        <article><strong>{data.sources.approved ?? 0}</strong><span>已完成三项审核</span></article>
        <article><strong>{data.ready_embeddings}</strong><span>可检索向量</span></article>
        <article><strong>{data.pending_embeddings}</strong><span>待处理向量</span></article>
        <article><strong>{activeJobs}</strong><span>运行中任务</span></article>
      </section>
      <section className="nutrition-runtime-card">
        <div>
          <p className="eyebrow">AGENT RUNTIME</p>
          <h2>{data.runtime.active_release_version ?? "尚未启用语料版本"}</h2>
          <p>只有这里显示的版本会参与线上 Agent 检索；草稿、候选和退役资料不会被混入。</p>
        </div>
        <dl>
          <div><dt>运行修订号</dt><dd>{data.runtime.runtime_revision}</dd></div>
          <div><dt>最后操作人</dt><dd>{data.runtime.updated_by}</dd></div>
          <div><dt>更新时间</dt><dd>{formatDate(data.runtime.updated_at)}</dd></div>
        </dl>
      </section>
      <section className="nutrition-capability-grid">
        <article><span>检索引擎</span><strong>{data.capabilities.rag_engine}</strong></article>
        <article><span>Embedding</span><strong>{data.capabilities.embedding_model}</strong></article>
        <article><span>Rerank</span><strong>{data.capabilities.rerank_model}</strong></article>
        <article><span>COS / Worker</span><strong>{data.capabilities.cos_configured ? "就绪" : "未配置"} / {data.capabilities.worker_enabled ? "运行" : "关闭"}</strong></article>
      </section>
    </>
  );
}

type ImportMode = "file" | "pasted_text" | "url";

function Sources() {
  const client = useQueryClient();
  const [mode, setMode] = useState<ImportMode>("file");
  const [sourceKey, setSourceKey] = useState("");
  const [version, setVersion] = useState("");
  const [title, setTitle] = useState("");
  const [publisher, setPublisher] = useState("");
  const [publishedAt, setPublishedAt] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [tags, setTags] = useState("");
  const [applicability, setApplicability] = useState("weight_loss");
  const [content, setContent] = useState("");
  const [remoteUrl, setRemoteUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [searchDraft, setSearchDraft] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const [selectedId, setSelectedId] = useState("");

  const query = useQuery({
    queryKey: ["nutrition-sources", status, search, offset],
    queryFn: () => api.nutritionSources({ status: status || undefined, search, offset }),
    refetchInterval: 8_000,
  });
  const importMutation = useMutation({
    mutationFn: async () => {
      const metadata = {
        source_key: sourceKey.trim(), version: version.trim(), title: title.trim(),
        publisher: publisher.trim(), published_at: publishedAt || null,
        source_url: sourceUrl.trim() || null, language: "zh-CN",
        tags: splitLabels(tags), applicability: splitLabels(applicability),
      };
      if (mode === "file") {
        if (!file) throw new Error("请选择要上传的文件");
        return api.importNutritionFile(metadata, file);
      }
      return api.importNutritionTextOrUrl({
        ...metadata, method: mode,
        content: mode === "pasted_text" ? content : null,
        remote_url: mode === "url" ? remoteUrl.trim() : null,
        filename: `${sourceKey.trim() || "nutrition-source"}.md`,
      });
    },
    onSuccess: async () => {
      setContent(""); setRemoteUrl(""); setFile(null);
      await Promise.all([
        client.invalidateQueries({ queryKey: ["nutrition-jobs"] }),
        client.invalidateQueries({ queryKey: ["nutrition-dashboard"] }),
      ]);
    },
  });
  const metadataReady = Boolean(sourceKey.trim() && version.trim() && title.trim() && publisher.trim());
  const inputReady = mode === "file" ? Boolean(file) : mode === "url" ? Boolean(remoteUrl.trim()) : Boolean(content.trim());

  return (
    <div className="nutrition-two-part">
      <details className="nutrition-import" open>
        <summary>导入一版新资料</summary>
        <form onSubmit={(event) => { event.preventDefault(); importMutation.mutate(); }}>
          <div className="nutrition-mode-switch">
            {(["file", "pasted_text", "url"] as ImportMode[]).map((value) => (
              <button type="button" className={mode === value ? "active" : ""} onClick={() => setMode(value)} key={value}>
                {{ file: "上传文件", pasted_text: "粘贴正文", url: "远程 URL" }[value]}
              </button>
            ))}
          </div>
          <div className="nutrition-form-grid">
            <label>资料标识<input required value={sourceKey} onChange={(event) => setSourceKey(event.target.value)} placeholder="例如 chinese_dietary_guidelines" /></label>
            <label>资料版本<input required value={version} onChange={(event) => setVersion(event.target.value)} placeholder="例如 2022-v1" /></label>
            <label>标题<input required value={title} onChange={(event) => setTitle(event.target.value)} /></label>
            <label>发布机构<input required value={publisher} onChange={(event) => setPublisher(event.target.value)} /></label>
            <label>发布日期<input type="date" value={publishedAt} onChange={(event) => setPublishedAt(event.target.value)} /></label>
            <label>权威来源链接<input type="url" value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="可留空" /></label>
            <label>标签<input value={tags} onChange={(event) => setTags(event.target.value)} placeholder="膳食指南, 高血压" /></label>
            <label>适用范围<input value={applicability} onChange={(event) => setApplicability(event.target.value)} placeholder="weight_loss, general_adult" /></label>
          </div>
          {mode === "file" && <label className="nutrition-file-input">原始文件（PDF / HTML / Markdown / 文本）<input type="file" accept=".pdf,.html,.htm,.md,.txt,text/*,application/pdf" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></label>}
          {mode === "pasted_text" && <label>资料正文<textarea rows={8} value={content} onChange={(event) => setContent(event.target.value)} /></label>}
          {mode === "url" && <label>公开文件 URL<input type="url" value={remoteUrl} onChange={(event) => setRemoteUrl(event.target.value)} placeholder="服务器将下载并固化到私有 COS" /></label>}
          {importMutation.error && <p className="nutrition-error">{errorText(importMutation.error)}</p>}
          {importMutation.data && <p className="nutrition-success">已创建处理任务 <code>{importMutation.data.job.id}</code>，可在“后台任务”查看解析与向量化进度。</p>}
          <button className="nutrition-primary" type="submit" disabled={!metadataReady || !inputReady || importMutation.isPending}>{importMutation.isPending ? "正在上传…" : "上传并开始索引"}</button>
        </form>
      </details>

      <section>
        <div className="nutrition-toolbar">
          <form onSubmit={(event) => { event.preventDefault(); setOffset(0); setSearch(searchDraft.trim()); }}>
            <input value={searchDraft} onChange={(event) => setSearchDraft(event.target.value)} placeholder="搜索标题、标识或发布机构" />
            <button type="submit">搜索</button>
          </form>
          <select value={status} onChange={(event) => { setOffset(0); setStatus(event.target.value); }}>
            <option value="">全部状态</option>
            {['draft', 'approved', 'published', 'rejected', 'retired'].map((value) => <option value={value} key={value}>{STATUS_LABELS[value]}</option>)}
          </select>
        </div>
        {query.isLoading && <Loading />}
        {query.error && <Failure error={query.error} />}
        {query.data && (
          <>
            <div className="nutrition-source-list">
              {query.data.items.map((item) => <SourceRow value={item} selected={selectedId === item.id} onSelect={() => setSelectedId(item.id)} key={item.id} />)}
              {query.data.items.length === 0 && <div className="state-card">没有符合条件的资料</div>}
            </div>
            <div className="pager">
              <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 30))}>上一页</button>
              <span>{offset + 1}–{Math.min(offset + 30, query.data.total)} / {query.data.total}</span>
              <button disabled={offset + 30 >= query.data.total} onClick={() => setOffset(offset + 30)}>下一页</button>
            </div>
          </>
        )}
      </section>
      {selectedId && <SourceInspector sourceId={selectedId} onClose={() => setSelectedId("")} />}
    </div>
  );
}

function SourceRow({ value, selected, onSelect }: { value: NutritionSourceSummary; selected: boolean; onSelect: () => void }) {
  return (
    <button type="button" className={`nutrition-source-row${selected ? " selected" : ""}`} onClick={onSelect}>
      <div><strong>{value.title}</strong><small>{value.source_key} · {value.version} · {value.publisher}</small></div>
      <div className="nutrition-source-counts"><span>{value.retrieval_chunk_count} chunks</span><span>{value.ready_embedding_count} vectors</span></div>
      <Status value={value.status} />
    </button>
  );
}

function SourceInspector({ sourceId, onClose }: { sourceId: string; onClose: () => void }) {
  const client = useQueryClient();
  const [view, setView] = useState<"sections" | "chunks">("sections");
  const [sectionOffset, setSectionOffset] = useState(0);
  const [chunkOffset, setChunkOffset] = useState(0);
  const [reviewType, setReviewType] = useState<NutritionReviewType>("content");
  const [reviewReason, setReviewReason] = useState("");
  const [retireReason, setRetireReason] = useState("");
  const detail = useQuery({ queryKey: ["nutrition-source", sourceId], queryFn: () => api.nutritionSource(sourceId) });
  const sections = useQuery({ queryKey: ["nutrition-source-sections", sourceId, sectionOffset], queryFn: () => api.nutritionSourceSections(sourceId, sectionOffset), enabled: view === "sections" });
  const chunks = useQuery({ queryKey: ["nutrition-source-chunks", sourceId, chunkOffset], queryFn: () => api.nutritionSourceChunks(sourceId, "retrieval_child", chunkOffset), enabled: view === "chunks" });
  const refresh = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: ["nutrition-source", sourceId] }),
      client.invalidateQueries({ queryKey: ["nutrition-sources"] }),
      client.invalidateQueries({ queryKey: ["nutrition-dashboard"] }),
    ]);
  };
  const review = useMutation({
    mutationFn: (decision: NutritionReviewDecision) => api.reviewNutritionSource(sourceId, reviewType, decision, reviewReason.trim() || null),
    onSuccess: async () => { setReviewReason(""); await refresh(); },
  });
  const retire = useMutation({
    mutationFn: () => api.retireNutritionSource(sourceId, retireReason.trim()),
    onSuccess: async () => { await refresh(); onClose(); },
  });
  const download = useMutation({
    mutationFn: () => api.downloadNutritionSource(sourceId),
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = detail.data?.asset?.original_filename ?? "nutrition-source";
      link.click();
      URL.revokeObjectURL(url);
    },
  });
  return (
    <aside className="nutrition-inspector">
      <header><div><p className="eyebrow">SOURCE INSPECTOR</p><h2>{detail.data?.source.title ?? "资料详情"}</h2></div><button type="button" onClick={onClose}>关闭</button></header>
      {detail.isLoading && <Loading />}
      {detail.error && <Failure error={detail.error} />}
      {detail.data && <SourceDetailSummary value={detail.data} />}
      {detail.data?.asset && <button className="nutrition-secondary" type="button" onClick={() => download.mutate()} disabled={download.isPending}>{download.isPending ? "下载中…" : "下载 COS 原始文件"}</button>}
      <section className="nutrition-review-panel">
        <h3>三项准入审核</h3>
        <div className="nutrition-review-state">
          {(Object.keys(REVIEW_LABELS) as NutritionReviewType[]).map((type) => <button type="button" className={reviewType === type ? "selected" : ""} onClick={() => setReviewType(type)} key={type}><span>{REVIEW_LABELS[type]}</span><Status value={detail.data?.review[type] ?? undefined} /></button>)}
        </div>
        <label>拒绝/撤销原因<textarea value={reviewReason} onChange={(event) => setReviewReason(event.target.value)} placeholder="批准时可留空；拒绝或撤销时必填" /></label>
        {review.error && <p className="nutrition-error">{errorText(review.error)}</p>}
        <div className="nutrition-actions">
          <button type="button" className="nutrition-primary" onClick={() => review.mutate("approve")} disabled={review.isPending}>批准此项</button>
          <button type="button" className="nutrition-danger" onClick={() => review.mutate("reject")} disabled={review.isPending || !reviewReason.trim()}>拒绝此项</button>
          <button type="button" className="nutrition-secondary" onClick={() => review.mutate("revoke")} disabled={review.isPending || !reviewReason.trim()}>撤销结论</button>
        </div>
      </section>
      <section>
        <div className="nutrition-subtabs">
          <button className={view === "sections" ? "active" : ""} onClick={() => setView("sections")}>解析章节</button>
          <button className={view === "chunks" ? "active" : ""} onClick={() => setView("chunks")}>检索切片</button>
        </div>
        {view === "sections" && <PagedContent query={sections} offset={sectionOffset} setOffset={setSectionOffset} kind="section" />}
        {view === "chunks" && <PagedContent query={chunks} offset={chunkOffset} setOffset={setChunkOffset} kind="chunk" />}
      </section>
      {detail.data && !["published", "retired"].includes(detail.data.source.status) && <section className="nutrition-retire"><h3>退役资料</h3><input value={retireReason} onChange={(event) => setRetireReason(event.target.value)} placeholder="说明退役原因" /><button type="button" className="nutrition-danger" disabled={!retireReason.trim() || retire.isPending} onClick={() => retire.mutate()}>确认退役</button>{retire.error && <p className="nutrition-error">{errorText(retire.error)}</p>}</section>}
    </aside>
  );
}

function SourceDetailSummary({ value }: { value: NutritionSourceDetail }) {
  return <section className="nutrition-source-summary"><div><Status value={value.source.status} /><code>{value.source.source_key} / {value.source.version}</code></div><dl><div><dt>发布机构</dt><dd>{value.source.publisher}</dd></div><div><dt>字符数</dt><dd>{value.source.char_count}</dd></div><div><dt>章节 / 切片</dt><dd>{value.section_count} / {value.chunk_count}</dd></div><div><dt>替代的旧版本</dt><dd><code>{value.source.supersedes_source_id ?? "首个版本"}</code></dd></div><div><dt>内容 Hash</dt><dd><code>{value.source.content_sha256}</code></dd></div></dl><div className="nutrition-labels">{value.labels.map((item) => <span key={`${item.kind}:${item.value}`}>{item.kind}: {item.value}</span>)}</div>{value.source.source_url && <a href={value.source.source_url} target="_blank" rel="noreferrer">打开权威来源 ↗</a>}<Technical value={value.reviews} label={`查看 ${value.reviews.length} 条审核历史`} /></section>;
}

type PageQuery = { isLoading: boolean; error: unknown; data?: { items: Array<{ id: string; content: string; heading_path?: string[]; kind?: string; ordinal: number; lexical_terms?: string }>; total: number } };
function PagedContent({ query, offset, setOffset, kind }: { query: PageQuery; offset: number; setOffset: (value: number) => void; kind: "section" | "chunk" }) {
  if (query.isLoading) return <Loading />;
  if (query.error) return <Failure error={query.error} />;
  return <><div className="nutrition-content-list">{query.data?.items.map((item) => <article key={item.id}><header><strong>#{item.ordinal + 1} {item.heading_path?.join(" / ") || item.kind || kind}</strong><code>{item.id}</code></header><p>{item.content}</p>{item.lexical_terms && <small>分词：{item.lexical_terms}</small>}</article>)}</div>{query.data && query.data.total > 20 && <div className="pager"><button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>上一页</button><span>{offset + 1}–{Math.min(offset + 20, query.data.total)} / {query.data.total}</span><button disabled={offset + 20 >= query.data.total} onClick={() => setOffset(offset + 20)}>下一页</button></div>}</>;
}

function Jobs() {
  const client = useQueryClient();
  const [selectedId, setSelectedId] = useState("");
  const jobs = useQuery({ queryKey: ["nutrition-jobs"], queryFn: api.nutritionJobs, refetchInterval: 2_000 });
  useEffect(() => { if (!selectedId && jobs.data?.items[0]) setSelectedId(jobs.data.items[0].id); }, [jobs.data, selectedId]);
  const detail = useQuery({ queryKey: ["nutrition-job", selectedId], queryFn: () => api.nutritionJob(selectedId), enabled: Boolean(selectedId), refetchInterval: 2_000 });
  const refresh = async () => { await Promise.all([client.invalidateQueries({ queryKey: ["nutrition-jobs"] }), client.invalidateQueries({ queryKey: ["nutrition-job"] }), client.invalidateQueries({ queryKey: ["nutrition-sources"] })]); };
  const retry = useMutation({ mutationFn: api.retryNutritionJob, onSuccess: refresh });
  const cancel = useMutation({ mutationFn: api.cancelNutritionJob, onSuccess: refresh });
  if (jobs.isLoading) return <Loading />;
  if (jobs.error) return <Failure error={jobs.error} />;
  return <div className="nutrition-split"><div className="nutrition-job-list">{jobs.data?.items.map((job) => <button type="button" className={selectedId === job.id ? "selected" : ""} onClick={() => setSelectedId(job.id)} key={job.id}><div><strong>{job.job_type}</strong><small>{job.stage} · {formatDate(job.created_at)}</small></div><Status value={job.status} /></button>)}{jobs.data?.items.length === 0 && <div className="state-card">暂无后台任务</div>}</div><section className="nutrition-job-detail">{detail.isLoading && <Loading />}{detail.error && <Failure error={detail.error} />}{detail.data && <JobDetail job={detail.data.job} events={detail.data.events} onRetry={() => retry.mutate(detail.data.job.id)} onCancel={() => cancel.mutate(detail.data.job.id)} busy={retry.isPending || cancel.isPending} />}</section></div>;
}

function JobDetail({ job, events, onRetry, onCancel, busy }: { job: NutritionJob; events: Array<{ id: string; sequence: number; stage: string; level: string; message: string; completed_items: number; total_items: number; created_at: string }>; onRetry: () => void; onCancel: () => void; busy: boolean }) {
  const percent = job.total_items > 0 ? Math.round(job.completed_items / job.total_items * 100) : job.status === "succeeded" ? 100 : 0;
  return <><header><div><p className="eyebrow">JOB DETAIL</p><h2>{job.job_type} · {job.stage}</h2></div><Status value={job.status} /></header><div className="nutrition-progress"><span style={{ width: `${percent}%` }} /></div><p>{job.completed_items} / {job.total_items || "?"} · 尝试 {job.attempt_count}/{job.max_attempts}</p>{job.safe_error_message && <p className="nutrition-error">{job.error_code}: {job.safe_error_message}</p>}<div className="nutrition-actions">{["failed", "cancelled"].includes(job.status) && <button className="nutrition-primary" type="button" disabled={busy} onClick={onRetry}>重试任务</button>}{["queued", "retry_wait"].includes(job.status) && <button className="nutrition-danger" type="button" disabled={busy} onClick={onCancel}>取消任务</button>}</div><div className="nutrition-events">{events.map((event) => <article key={event.id}><span>{event.sequence}</span><div><strong>{event.message}</strong><small>{event.stage} · {event.level} · {formatDate(event.created_at)}</small></div></article>)}</div><Technical value={{ input: job.input, output: job.output }} /></>;
}

function RetrievalLab() {
  const client = useQueryClient();
  const [queryText, setQueryText] = useState("");
  const [releaseId, setReleaseId] = useState("");
  const [tags, setTags] = useState("");
  const [applicability, setApplicability] = useState("");
  const [publishers, setPublishers] = useState("");
  const [maxResults, setMaxResults] = useState(10);
  const [runId, setRunId] = useState("");
  const [baseDatasetId, setBaseDatasetId] = useState("");
  const [datasetVersion, setDatasetVersion] = useState("");
  const [caseKey, setCaseKey] = useState("");
  const [expectedSources, setExpectedSources] = useState("");
  const [expectedConcepts, setExpectedConcepts] = useState("");
  const [forbiddenSources, setForbiddenSources] = useState("");
  const [expectedOutcome, setExpectedOutcome] = useState<"evidence" | "insufficient">("evidence");
  const releases = useQuery({ queryKey: ["nutrition-releases"], queryFn: api.nutritionReleases });
  const datasets = useQuery({ queryKey: ["nutrition-evaluation-datasets"], queryFn: api.nutritionEvaluationDatasets });
  const run = useQuery({ queryKey: ["nutrition-retrieval-run", runId], queryFn: () => api.nutritionRetrievalLabRun(runId), enabled: Boolean(runId) });
  const search = useMutation({
    mutationFn: () => api.runNutritionRetrievalLab({ query: queryText.trim(), release_id: releaseId || null, max_results: maxResults, metadata_filter: { tags: splitLabels(tags), applicability: splitLabels(applicability), publishers: splitLabels(publishers) } }),
    onSuccess: (result) => {
      setRunId(result.retrieval_run_id ?? "");
      setExpectedSources("");
      setDatasetVersion(`nutrition_eval_${new Date().toISOString().replaceAll(/[-:.TZ]/g, "").slice(0, 14)}`);
      setCaseKey(`lab-${Date.now()}`);
    },
  });
  const appendCase = useMutation({
    mutationFn: () => api.appendNutritionLabEvaluationCase(runId, {
      base_dataset_id: baseDatasetId || null,
      dataset_version: datasetVersion.trim(),
      case_key: caseKey.trim(),
      expected_source_keys: splitLabels(expectedSources),
      expected_chunk_concepts: splitLabels(expectedConcepts),
      forbidden_source_keys: splitLabels(forbiddenSources),
      expected_outcome: expectedOutcome,
    }),
    onSuccess: async (result) => {
      setBaseDatasetId(result.dataset.id);
      await client.invalidateQueries({ queryKey: ["nutrition-evaluation-datasets"] });
    },
  });
  useEffect(() => {
    if (!run.data || expectedSources) return;
    setExpectedSources(Array.from(new Set(run.data.candidates.filter((item) => item.selection_status === "adopted").map((item) => item.source_key))).join(", "));
  }, [run.data, expectedSources]);
  return <>
    <section className="nutrition-lab-form">
      <div><p className="eyebrow">RETRIEVAL LAB</p><h2>检查每条检索通道</h2><p>默认测试当前线上版本，也可以指定尚未启用的候选版本。元数据过滤发生在向量检索之前。</p></div>
      <form onSubmit={(event) => { event.preventDefault(); search.mutate(); }}>
        <label className="wide">测试问题<textarea required value={queryText} onChange={(event) => setQueryText(event.target.value)} placeholder="例如：减脂期间晚餐怎样搭配更合适？" /></label>
        <label>语料版本<select value={releaseId} onChange={(event) => setReleaseId(event.target.value)}><option value="">当前线上版本</option>{releases.data?.items.map((release) => <option value={release.id} key={release.id}>{release.version} · {STATUS_LABELS[release.status] ?? release.status}</option>)}</select></label>
        <label>最多返回<input type="number" min={1} max={20} value={maxResults} onChange={(event) => setMaxResults(Number(event.target.value))} /></label>
        <label>标签过滤<input value={tags} onChange={(event) => setTags(event.target.value)} placeholder="逗号分隔" /></label>
        <label>适用范围过滤<input value={applicability} onChange={(event) => setApplicability(event.target.value)} placeholder="weight_loss" /></label>
        <label>发布机构过滤<input value={publishers} onChange={(event) => setPublishers(event.target.value)} /></label>
        <button className="nutrition-primary" type="submit" disabled={!queryText.trim() || search.isPending}>{search.isPending ? "检索中…" : "运行检索"}</button>
      </form>
      {search.error && <p className="nutrition-error">{errorText(search.error)}</p>}
      {search.data && !search.data.retrieval_run_id && <p className="nutrition-warning">未产生检索记录：{search.data.query_summary}</p>}
    </section>
    {run.isLoading && <Loading label="正在读取通道分数" />}
    {run.error && <Failure error={run.error} />}
    {run.data && <section className="nutrition-lab-results">
      <header><div><h2>检索结果</h2><p>{run.data.safe_query_summary}</p></div><div><Status value={run.data.status} /><span>{run.data.total_latency_ms} ms</span></div></header>
      <div className="nutrition-score-legend"><span>Dense：语义向量</span><span>Lexical：中文全文</span><span>Phrase：短语命中</span><span>RRF：融合</span><span>Rerank：最终相关性</span></div>
      {run.data.candidates.map((candidate) => <article className={candidate.selection_status === "adopted" ? "adopted" : "rejected"} key={candidate.id}><header><div><strong>{candidate.source_title}</strong><small>{candidate.source_key} · chunk {candidate.chunk_id}</small></div><Status value={candidate.selection_status} /></header><div className="nutrition-channel-scores"><span>D #{candidate.dense_rank ?? "—"} · {candidate.dense_score?.toFixed(3) ?? "—"}</span><span>L #{candidate.lexical_rank ?? "—"} · {candidate.lexical_score?.toFixed(3) ?? "—"}</span><span>P #{candidate.phrase_rank ?? "—"} · {candidate.phrase_score?.toFixed(3) ?? "—"}</span><span>RRF #{candidate.rrf_rank} · {candidate.rrf_score.toFixed(3)}</span><span>RR #{candidate.rerank_rank ?? "—"} · {candidate.rerank_score?.toFixed(3) ?? "—"}</span></div>{candidate.rejection_reason && <p className="nutrition-rejection">未采用：{candidate.rejection_reason}</p>}<p>{candidate.child_content}</p><details><summary>查看送给 Agent 的父级上下文</summary><p>{candidate.parent_context}</p></details></article>)}
      <Technical value={{ query_plan: run.data.query_plan, provider_usage: run.data.provider_usage, query_hash: run.data.query_hash }} />
      <details className="nutrition-lab-case">
        <summary>把这次结果追加为回归评测 Case</summary>
        <p>评测集不可修改：系统会复制所选基础版本并追加本条，生成一个新的冻结版本。</p>
        <div className="nutrition-form-grid">
          <label>基础评测集<select value={baseDatasetId} onChange={(event) => setBaseDatasetId(event.target.value)}><option value="">不复制，从 1 条新建</option>{datasets.data?.items.map((item) => <option value={item.id} key={item.id}>{item.version} · {item.cases.length} cases</option>)}</select></label>
          <label>新评测集版本<input value={datasetVersion} onChange={(event) => setDatasetVersion(event.target.value)} /></label>
          <label>Case 标识<input value={caseKey} onChange={(event) => setCaseKey(event.target.value)} /></label>
          <label>期望结论<select value={expectedOutcome} onChange={(event) => setExpectedOutcome(event.target.value as "evidence" | "insufficient")}><option value="evidence">应该命中证据</option><option value="insufficient">应该判定证据不足</option></select></label>
          <label>期望资料标识<input value={expectedSources} onChange={(event) => setExpectedSources(event.target.value)} placeholder="逗号分隔" /></label>
          <label>期望正文概念<input value={expectedConcepts} onChange={(event) => setExpectedConcepts(event.target.value)} placeholder="逗号分隔" /></label>
          <label>禁止出现的资料<input value={forbiddenSources} onChange={(event) => setForbiddenSources(event.target.value)} placeholder="逗号分隔" /></label>
        </div>
        {appendCase.error && <p className="nutrition-error">{errorText(appendCase.error)}</p>}
        {appendCase.data && <p className="nutrition-success">已生成评测集 {appendCase.data.dataset.version}，共 {appendCase.data.dataset.cases.length} 条。</p>}
        <button type="button" className="nutrition-primary" disabled={!datasetVersion.trim() || !caseKey.trim() || appendCase.isPending} onClick={() => appendCase.mutate()}>创建新评测集版本</button>
      </details>
    </section>}
  </>;
}

function Releases() {
  const client = useQueryClient();
  const [version, setVersion] = useState("");
  const [sourceIds, setSourceIds] = useState<string[]>([]);
  const [datasetByRelease, setDatasetByRelease] = useState<Record<string, string>>({});
  const [reasonByRelease, setReasonByRelease] = useState<Record<string, string>>({});
  const releases = useQuery({ queryKey: ["nutrition-releases"], queryFn: api.nutritionReleases, refetchInterval: 5_000 });
  const sources = useQuery({ queryKey: ["nutrition-approved-sources"], queryFn: () => api.nutritionSources({ status: "approved" }) });
  const datasets = useQuery({ queryKey: ["nutrition-evaluation-datasets"], queryFn: api.nutritionEvaluationDatasets });
  const runtime = useQuery({ queryKey: ["nutrition-runtime"], queryFn: api.nutritionRuntime, refetchInterval: 5_000 });
  const evaluationRuns = useQuery({ queryKey: ["nutrition-evaluation-runs"], queryFn: api.nutritionEvaluationRuns, refetchInterval: 5_000 });
  const activeRelease = releases.data?.items.find((item) => item.id === runtime.data?.runtime.active_release_id);
  const sourceTitles = Object.fromEntries((sources.data?.items ?? []).map((item) => [item.id, item.title]));
  const refresh = async () => { await Promise.all([client.invalidateQueries({ queryKey: ["nutrition-releases"] }), client.invalidateQueries({ queryKey: ["nutrition-runtime"] }), client.invalidateQueries({ queryKey: ["nutrition-jobs"] }), client.invalidateQueries({ queryKey: ["nutrition-evaluation-runs"] }), client.invalidateQueries({ queryKey: ["nutrition-dashboard"] })]); };
  const create = useMutation({ mutationFn: () => api.createNutritionRelease(version.trim(), sourceIds), onSuccess: async () => { setVersion(""); setSourceIds([]); await refresh(); } });
  const evaluate = useMutation({ mutationFn: ({ releaseId, datasetId }: { releaseId: string; datasetId: string }) => api.evaluateNutritionRelease(releaseId, datasetId), onSuccess: refresh });
  const review = useMutation({ mutationFn: ({ releaseId, decision }: { releaseId: string; decision: "approve" | "reject" }) => api.reviewNutritionRelease(releaseId, decision, reasonByRelease[releaseId]?.trim() || null), onSuccess: refresh });
  const activate = useMutation({ mutationFn: (releaseId: string) => api.activateNutritionRelease(releaseId, reasonByRelease[releaseId].trim()), onSuccess: refresh });
  const rollback = useMutation({ mutationFn: (releaseId: string) => api.rollbackNutritionRuntime(releaseId, reasonByRelease[releaseId].trim()), onSuccess: refresh });
  const actionError = create.error || evaluate.error || review.error || activate.error || rollback.error;
  return <><section className="nutrition-runtime-card compact"><div><p className="eyebrow">ACTIVE RELEASE</p><h2>{runtime.data?.runtime.active_release_version ?? "尚未启用"}</h2><p>revision {runtime.data?.runtime.runtime_revision ?? 0}</p></div><p>发布不会自动影响 Agent；只有“全量启用”会原子切换运行时指针。旧版本保留，可一键回滚。</p></section><section className="nutrition-release-builder"><h2>创建候选语料版本</h2><p>只能选择已经完成内容、适用范围、版权三项审核且向量就绪的资料。</p><label>版本名<input value={version} onChange={(event) => setVersion(event.target.value)} placeholder="例如 nutrition_2026_01" /></label><div className="nutrition-source-picker">{sources.data?.items.map((source) => <label key={source.id}><input type="checkbox" checked={sourceIds.includes(source.id)} onChange={(event) => setSourceIds(event.target.checked ? [...sourceIds, source.id] : sourceIds.filter((id) => id !== source.id))} /><span><strong>{source.title}</strong><small>{source.source_key} · {source.version}</small></span></label>)}{sources.data?.items.length === 0 && <p>暂无可发布资料，请先完成资料审核。</p>}</div><button type="button" className="nutrition-primary" onClick={() => create.mutate()} disabled={!version.trim() || sourceIds.length === 0 || create.isPending}>冻结 Manifest 并创建版本</button></section>{actionError && <p className="nutrition-error">{errorText(actionError)}</p>}{releases.isLoading && <Loading />}{releases.error && <Failure error={releases.error} />}<section className="nutrition-release-list">{releases.data?.items.map((release) => <ReleaseCard release={release} datasets={datasets.data?.items ?? []} activeReleaseId={runtime.data?.runtime.active_release_id ?? null} activeSourceIds={activeRelease?.source_ids ?? []} sourceTitles={sourceTitles} evaluation={evaluationRuns.data?.items.find((item) => item.id === release.evaluation_run_id) ?? null} datasetId={datasetByRelease[release.id] ?? ""} setDatasetId={(value) => setDatasetByRelease((current) => ({ ...current, [release.id]: value }))} reason={reasonByRelease[release.id] ?? ""} setReason={(value) => setReasonByRelease((current) => ({ ...current, [release.id]: value }))} onEvaluate={() => evaluate.mutate({ releaseId: release.id, datasetId: datasetByRelease[release.id] })} onReview={(decision) => review.mutate({ releaseId: release.id, decision })} onActivate={() => activate.mutate(release.id)} onRollback={() => rollback.mutate(release.id)} busy={evaluate.isPending || review.isPending || activate.isPending || rollback.isPending} key={release.id} />)}{releases.data?.items.length === 0 && <div className="state-card">尚未创建语料版本</div>}</section></>;
}

function ReleaseCard({ release, datasets, activeReleaseId, activeSourceIds, sourceTitles, evaluation, datasetId, setDatasetId, reason, setReason, onEvaluate, onReview, onActivate, onRollback, busy }: { release: NutritionRelease; datasets: Array<{ id: string; version: string; status: string; cases: unknown[] }>; activeReleaseId: string | null; activeSourceIds: string[]; sourceTitles: Record<string, string>; evaluation: NutritionEvaluationRun | null; datasetId: string; setDatasetId: (value: string) => void; reason: string; setReason: (value: string) => void; onEvaluate: () => void; onReview: (decision: "approve" | "reject") => void; onActivate: () => void; onRollback: () => void; busy: boolean }) {
  const isActive = activeReleaseId === release.id;
  const [confirmation, setConfirmation] = useState("");
  const added = release.source_ids.filter((id) => !activeSourceIds.includes(id));
  const removed = activeSourceIds.filter((id) => !release.source_ids.includes(id));
  const requiresRuntimeConfirmation = release.status === "approved" || release.status === "retired";
  return <article><header><div><h2>{release.version}</h2><code>{release.manifest_sha256}</code></div><Status value={release.status} /></header><div className="nutrition-release-facts"><span>{release.source_ids.length} 份资料</span><span>{release.retrieval_profile_id}</span><span>{formatDate(release.created_at)}</span></div><section className="nutrition-release-diff"><strong>与当前线上版本比较</strong><p>新增：{added.length ? added.map((id) => sourceTitles[id] ?? id).join("、") : "无"}</p><p>移除：{removed.length ? removed.map((id) => sourceTitles[id] ?? id).join("、") : "无"}</p></section>{evaluation && <section className="nutrition-release-evaluation"><div><strong>自动评测</strong><Status value={evaluation.status} /></div><Technical value={evaluation.metrics} label="查看质量指标与发布门槛" /></section>}<label>操作说明 / 拒绝原因<input value={reason} onChange={(event) => setReason(event.target.value)} placeholder="启用、回滚、拒绝时必填；批准可留空" /></label>{["evaluating", "indexing_check", "review_ready"].includes(release.status) && <div className="nutrition-evaluate"><select value={datasetId} onChange={(event) => setDatasetId(event.target.value)}><option value="">选择冻结评测集</option>{datasets.map((dataset) => <option value={dataset.id} disabled={dataset.status !== "ready"} key={dataset.id}>{dataset.version} · {dataset.cases.length} cases · {STATUS_LABELS[dataset.status] ?? dataset.status}</option>)}</select><button type="button" className="nutrition-primary" onClick={onEvaluate} disabled={!datasetId || busy}>运行离线评测</button></div>}{requiresRuntimeConfirmation && <label className="nutrition-runtime-confirm">高风险操作确认<input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} placeholder={`输入版本名 ${release.version}`} /><small>此操作会改变 Agent 使用的唯一 Active Release；当前版本仍保留用于回滚。</small></label>}<div className="nutrition-actions">{release.status === "review_ready" && <><button type="button" className="nutrition-primary" onClick={() => onReview("approve")} disabled={busy}>人工验收通过</button><button type="button" className="nutrition-danger" onClick={() => onReview("reject")} disabled={busy || !reason.trim()}>拒绝版本</button></>}{release.status === "approved" && <button type="button" className="nutrition-primary" onClick={onActivate} disabled={busy || !reason.trim() || confirmation !== release.version}>确认全量启用</button>}{release.status === "retired" && <button type="button" className="nutrition-secondary" onClick={onRollback} disabled={busy || !reason.trim() || confirmation !== release.version}>确认回滚到此版本</button>}{isActive && <span className="nutrition-active-note">Agent 当前正在使用</span>}</div><Technical value={release} /></article>;
}

const SAMPLE_CASES = [
  {
    case_key: "weight-loss-dinner-001",
    query_plan_input: { query: "减脂期间晚餐怎么搭配？", metadata_filter: { applicability: ["weight_loss"] } },
    expected_source_keys: ["replace-with-source-key"],
    expected_chunk_concepts: ["食物多样", "合理搭配"],
    forbidden_source_keys: [],
    expected_outcome: "evidence",
  },
  {
    case_key: "unsupported-extreme-diet-001",
    query_plan_input: { query: "只喝水七天能减多少斤？", metadata_filter: {} },
    expected_source_keys: [],
    expected_chunk_concepts: [],
    forbidden_source_keys: [],
    expected_outcome: "insufficient",
  },
];

function Evaluation() {
  const client = useQueryClient();
  const [version, setVersion] = useState("");
  const [rawCases, setRawCases] = useState(JSON.stringify(SAMPLE_CASES, null, 2));
  const [selectedRunId, setSelectedRunId] = useState("");
  const datasets = useQuery({ queryKey: ["nutrition-evaluation-datasets"], queryFn: api.nutritionEvaluationDatasets });
  const runs = useQuery({ queryKey: ["nutrition-evaluation-runs"], queryFn: api.nutritionEvaluationRuns, refetchInterval: 3_000 });
  const run = useQuery({ queryKey: ["nutrition-evaluation-run", selectedRunId], queryFn: () => api.nutritionEvaluationRun(selectedRunId), enabled: Boolean(selectedRunId), refetchInterval: 3_000 });
  const parsed = useMemo(() => {
    try { const value = JSON.parse(rawCases) as Array<Omit<NutritionEvaluationCase, "id">>; return Array.isArray(value) ? value : null; } catch { return null; }
  }, [rawCases]);
  const create = useMutation({ mutationFn: () => api.createNutritionEvaluationDataset(version.trim(), parsed ?? []), onSuccess: async () => { setVersion(""); await client.invalidateQueries({ queryKey: ["nutrition-evaluation-datasets"] }); } });
  return <><section className="nutrition-eval-guide"><h2>质量门槛</h2><p>可用于发布验收的冻结评测集至少需要 100 条，其中“证据不足”负例不少于 25%。门槛固定为 Recall@5 ≥ 90%、Recall@10 ≥ 95%、不足证据判断精确率 ≥ 95%、禁用来源泄漏为 0、引用完整率 100%。</p></section><details className="nutrition-import"><summary>创建不可变评测集</summary><form onSubmit={(event: FormEvent) => { event.preventDefault(); create.mutate(); }}><label>评测集版本<input value={version} onChange={(event) => setVersion(event.target.value)} placeholder="例如 nutrition_eval_2026_01" /></label><label>Cases JSON<textarea className="nutrition-code-editor" rows={18} value={rawCases} onChange={(event) => setRawCases(event.target.value)} /></label><p className={parsed ? "nutrition-success" : "nutrition-error"}>{parsed ? `JSON 有效：${parsed.length} 条；${parsed.filter((item) => item.expected_outcome === "insufficient").length} 条证据不足负例。` : "JSON 格式无效。"}</p>{create.error && <p className="nutrition-error">{errorText(create.error)}</p>}<button className="nutrition-primary" type="submit" disabled={!version.trim() || !parsed?.length || create.isPending}>冻结评测集</button></form></details><div className="nutrition-split"><section><div className="section-heading"><div><h2>评测集</h2><p>草稿集可继续作为模板，但不能阻断或放行版本。</p></div></div><div className="nutrition-dataset-list">{datasets.data?.items.map((dataset) => <article key={dataset.id}><header><strong>{dataset.version}</strong><Status value={dataset.status} /></header><p>{dataset.cases.length} cases · {dataset.cases.filter((item) => item.expected_outcome === "insufficient").length} negative</p><code>{dataset.manifest_sha256}</code><Technical value={dataset.cases.slice(0, 10)} label="预览前 10 条" /></article>)}</div></section><section><div className="section-heading"><div><h2>评测运行</h2><p>点击一条记录查看指标和逐 Case 结果。</p></div></div><div className="nutrition-run-list">{runs.data?.items.map((item) => <button type="button" className={selectedRunId === item.id ? "selected" : ""} onClick={() => setSelectedRunId(item.id)} key={item.id}><div><strong>{item.status}</strong><small>{formatDate(item.created_at)}</small></div><Status value={item.status} /></button>)}</div>{run.isLoading && <Loading />}{run.error && <Failure error={run.error} />}{run.data && <div className="nutrition-eval-result"><h3>评测指标</h3><Technical value={run.data.metrics} label="查看全部指标" /><p>{run.data.results?.filter((item) => item.passed).length ?? 0} / {run.data.results?.length ?? 0} Case 通过</p><Technical value={run.data.results} label="查看逐 Case 结果" /></div>}</section></div></>;
}
