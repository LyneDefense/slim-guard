import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api } from "../../api";
import type {
  StyleIterationClassification,
  StyleIterationRun,
  StyleIterationStatus,
  StyleExampleData,
  StyleProfileData,
  StyleRegressionCaseData,
  StyleRuntimeContext,
} from "../../types";

const STATUS_LABELS: Record<StyleIterationStatus, string> = {
  queued: "排队中",
  validating: "校验来源",
  freezing_inputs: "冻结素材",
  classifying: "分类反馈",
  building_profile: "生成候选风格",
  generating_cases: "生成回归场景",
  evaluating: "生成并自动评测 A/B",
  importing_review: "导入人工评审",
  ready_for_review: "等待人工评审",
  needs_action: "需要人工处理",
  failed_transient: "临时失败",
  failed_terminal: "构建失败",
  rejected: "人工拒绝",
  evaluated: "已发布",
  active: "已启用",
  cancelled: "已取消",
};

const PROCESSING = new Set<StyleIterationStatus>([
  "queued",
  "validating",
  "freezing_inputs",
  "classifying",
  "building_profile",
  "generating_cases",
  "evaluating",
  "importing_review",
]);

const CATEGORY_LABELS: Record<string, string> = {
  style_existing_act: "已有表达行为",
  style_composite: "多个已有行为",
  new_act_required: "需要扩展沟通行为",
  upstream_logic: "属于上游业务判断",
  professional_content: "包含专业内容",
  privacy_or_identity: "隐私或真人身份风险",
  invalid_or_conflicting: "无效或相互矛盾",
};

function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "操作失败，请刷新后重试。";
}

export function StyleIterationPage() {
  const queryClient = useQueryClient();
  const context = useQuery({
    queryKey: ["style-iteration-context"],
    queryFn: api.styleIterationContext,
    refetchInterval: 2_000,
  });
  const history = useQuery({
    queryKey: ["style-iterations"],
    queryFn: api.styleIterations,
    refetchInterval: 4_000,
  });
  const runtime = useQuery({
    queryKey: ["style-runtime"],
    queryFn: api.styleRuntime,
    refetchInterval: 4_000,
  });
  const [sourceVersion, setSourceVersion] = useState("");
  const [selectedRunId, setSelectedRunId] = useState("");
  const [sourceConfirmed, setSourceConfirmed] = useState(false);

  useEffect(() => {
    if (!sourceVersion && context.data?.suggested_source_version) {
      setSourceVersion(context.data.suggested_source_version);
    }
  }, [context.data, sourceVersion]);
  useEffect(() => {
    const preferred = context.data?.open_run?.run_id ?? history.data?.items[0]?.run_id;
    if (!selectedRunId && preferred) setSelectedRunId(preferred);
  }, [context.data, history.data, selectedRunId]);

  const eligibility = useQuery({
    queryKey: ["style-iteration-eligibility", sourceVersion],
    queryFn: () => api.styleIterationEligibility(sourceVersion),
    enabled: Boolean(sourceVersion),
    refetchInterval: 3_000,
  });
  const selected = useQuery({
    queryKey: ["style-iteration", selectedRunId],
    queryFn: () => api.styleIteration(selectedRunId),
    enabled: Boolean(selectedRunId),
    refetchInterval: 2_000,
  });
  const events = useQuery({
    queryKey: ["style-iteration-events", selectedRunId],
    queryFn: () => api.styleIterationEvents(selectedRunId),
    enabled: Boolean(selectedRunId),
    refetchInterval: 2_000,
  });

  const invalidateAll = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["style-iteration-context"] }),
      queryClient.invalidateQueries({ queryKey: ["style-iteration-eligibility"] }),
      queryClient.invalidateQueries({ queryKey: ["style-iterations"] }),
      queryClient.invalidateQueries({ queryKey: ["style-iteration"] }),
      queryClient.invalidateQueries({ queryKey: ["style-iteration-events"] }),
      queryClient.invalidateQueries({ queryKey: ["style-runtime"] }),
      queryClient.invalidateQueries({ queryKey: ["style-ab-context"] }),
    ]);
  };
  const build = useMutation({
    mutationFn: () => api.createStyleIteration(sourceVersion, crypto.randomUUID()),
    onSuccess: async (run) => {
      setSelectedRunId(run.run_id);
      setSourceConfirmed(false);
      await invalidateAll();
    },
  });

  return (
    <div className="page style-iteration-page">
      <header className="page-header">
        <div>
          <p className="eyebrow">STYLE PROFILE · VERSION PIPELINE</p>
          <h1>风格版本</h1>
          <p>从实名评审和风格纠正构建下一版；构建、发布、启用是三个独立关卡。</p>
        </div>
        {runtime.data && (
          <div className="style-runtime-chip">
            <small>当前全量版本</small>
            <strong>{runtime.data.runtime.active_profile_version}</strong>
            <span>revision {runtime.data.runtime.revision}</span>
          </div>
        )}
      </header>

      {runtime.data && <RuntimeOverview value={runtime.data} />}

      <section className="style-build-panel">
        <div>
          <p className="eyebrow">BUILD NEXT VERSION</p>
          <h2>自主构建下一版本</h2>
          <p>系统会冻结该版本的全部最新 A/B 结论和新增纠正，再生成新的候选 Profile 与回归 Case；不会覆盖旧版本。</p>
        </div>
        <label>来源版本
          <select
            value={sourceVersion}
            onChange={(event) => {
              setSourceVersion(event.target.value);
              setSourceConfirmed(false);
            }}
          >
            {context.data?.candidate_profile_versions.map((version) => (
              <option value={version} key={version}>{version}</option>
            ))}
            {sourceVersion && !context.data?.candidate_profile_versions.includes(sourceVersion) && (
              <option value={sourceVersion}>{sourceVersion}</option>
            )}
          </select>
        </label>
        {eligibility.data && (
          <>
            <div className="style-build-counts">
              <span>{eligibility.data.counts.reviewed_case_count} 条已评 A/B</span>
              <span>{eligibility.data.counts.rejected_case_count} 条拒绝</span>
              <span>{eligibility.data.counts.style_correction_count} 条新增纠正</span>
            </div>
            <small>
              预计生成 {eligibility.data.estimate.case_count_min}–{eligibility.data.estimate.case_count_max} 条回归 Case，
              调用模型 {eligibility.data.estimate.model_call_min}–{eligibility.data.estimate.model_call_max} 次。
              实际数量由新纠正的分类结果决定。
            </small>
          </>
        )}
        {eligibility.data && !eligibility.data.eligible && (
          <ul className="style-build-blockers">
            {eligibility.data.reasons.map((reason) => <li key={reason}>{reason}</li>)}
          </ul>
        )}
        <label className="style-build-confirmation">
          <input
            type="checkbox"
            checked={sourceConfirmed}
            onChange={(event) => setSourceConfirmed(event.target.checked)}
          />
          我确认该版本的人工评语和风格纠正已完成脱敏及纯表达审查
        </label>
        {build.error && <p className="style-action-error">{errorText(build.error)}</p>}
        <button
          type="button"
          onClick={() => build.mutate()}
          disabled={
            build.isPending
            || !context.data?.model_configured
            || Boolean(context.data?.open_run)
            || !eligibility.data?.eligible
            || !sourceConfirmed
          }
        >
          {build.isPending ? "正在创建任务…" : "构建下一版本"}
        </button>
        {!context.data?.model_configured && <small>模型未配置，当前不能构建。</small>}
        {context.data?.open_run && <small>已有一个未结束版本，需先完成、处理或取消。</small>}
      </section>

      <div className="style-version-layout">
        <aside className="style-version-history">
          <div><h2>版本历史</h2><span>{history.data?.total ?? 0}</span></div>
          {history.data?.items.map((run) => (
            <button
              type="button"
              className={run.run_id === selectedRunId ? "active" : ""}
              onClick={() => setSelectedRunId(run.run_id)}
              key={run.run_id}
            >
              <strong>{run.target_version}</strong>
              <span>{STATUS_LABELS[run.status]}</span>
              <small>来自 {run.source_version}</small>
            </button>
          ))}
        </aside>
        <main className="style-version-detail">
          {selected.isLoading && <div className="state-card">正在读取版本构建…</div>}
          {selected.error && <div className="state-card state-error">{errorText(selected.error)}</div>}
          {selected.data && (
            <StyleIterationDetail
              run={selected.data}
              events={events.data?.items ?? []}
              runtime={runtime.data?.runtime}
              directActivationAllowed={runtime.data?.direct_activation_allowed ?? false}
              onChanged={invalidateAll}
            />
          )}
          {!selectedRunId && <div className="state-card">还没有风格版本构建记录。</div>}
        </main>
      </div>
    </div>
  );
}

function StyleIterationDetail({
  run,
  events,
  runtime,
  directActivationAllowed,
  onChanged,
}: {
  run: StyleIterationRun;
  events: Array<{ sequence: number; summary: string; created_at: string; event_type: string }>;
  runtime?: { active_profile_version: string; previous_profile_version: string | null; revision: number };
  directActivationAllowed: boolean;
  onChanged: () => Promise<void>;
}) {
  const [reason, setReason] = useState("");
  const [publishConfirmed, setPublishConfirmed] = useState(false);
  const action = useMutation({
    mutationFn: async (kind: "publish" | "activate" | "rollback" | "retry" | "cancel") => {
      if (kind === "publish") return api.publishStyleIteration(run.run_id);
      if (kind === "activate") {
        if (!runtime) throw new Error("运行时状态尚未读取");
        return api.activateStyleVersion(run.target_version, runtime.revision, reason.trim());
      }
      if (kind === "rollback") {
        if (!runtime) throw new Error("运行时状态尚未读取");
        return api.rollbackStyleVersion(runtime.revision, reason.trim());
      }
      if (kind === "retry") return api.retryStyleIteration(run.run_id, reason.trim());
      return api.cancelStyleIteration(run.run_id, reason.trim());
    },
    onSuccess: onChanged,
  });
  const progress = run.progress.total
    ? Math.round((run.progress.current / run.progress.total) * 100)
    : 0;
  const candidate = run.artifacts?.profile;
  const candidateExamples = run.artifacts?.bundle?.examples ?? [];
  const isActive = runtime?.active_profile_version === run.target_version;

  const activate = () => {
    if (!runtime) return;
    const confirmed = window.confirm(
      `确认全量启用 ${run.target_version}？\n当前版本：${runtime.active_profile_version}\n回滚版本：${runtime.previous_profile_version ?? runtime.active_profile_version}`,
    );
    if (confirmed) action.mutate("activate");
  };

  return (
    <>
      <section className="style-version-summary">
        <header>
          <div>
            <p className="eyebrow">{run.run_id}</p>
            <h2>{run.target_version}</h2>
            <p>来源 {run.source_version} · 创建人 {run.created_by} · {formatDate(run.created_at)}</p>
          </div>
          <span className={`style-version-status status-${run.status}`}>{STATUS_LABELS[run.status]}</span>
        </header>
        <div className="style-progress"><span style={{ width: `${progress}%` }} /></div>
        <small>{run.progress.current} / {run.progress.total} · {progress}% · 当前阶段 {run.stage}</small>
        {run.failure && <div className="style-action-error"><strong>{run.failure.code}</strong> · {run.failure.summary}</div>}
        {PROCESSING.has(run.status) && <p className="style-wait-note">后台正在工作，页面会自动刷新；模型生成和自动评测通常需要几分钟。</p>}
      </section>

      {run.review && run.review.counts.case_count > 0 && (
        <section className="style-review-gate">
          <div><strong>{run.review.counts.reviewed_case_count} / {run.review.counts.case_count}</strong><span>人工已评</span></div>
          <div><strong>{run.review.counts.accepted_case_count}</strong><span>接受</span></div>
          <div><strong>{run.review.counts.rejected_case_count}</strong><span>拒绝</span></div>
          <div><strong>{run.review.automated_passed ? "通过" : "未通过"}</strong><span>自动评测</span></div>
          <Link to={`/style-ab?version=${encodeURIComponent(run.target_version)}`}>进入该版本 A/B 人评 →</Link>
        </section>
      )}

      {candidate && (
        <ProfileDiff
          source={run.source_profile ?? null}
          sourceExamples={run.source_examples ?? []}
          candidate={candidate}
          candidateExamples={candidateExamples}
        />
      )}

      {run.artifacts?.cases && run.artifacts.comparison && (
        <RegressionPanel
          cases={run.artifacts.cases}
          results={run.artifacts.comparison.evaluation.results}
        />
      )}

      {run.artifacts?.classification && (
        <ClassificationPanel items={run.artifacts.classification} />
      )}

      <section className="style-version-actions">
        <div><h3>版本操作</h3><p>发布只保存不可变版本；启用才会改变后续多 Agent Turn 使用的默认风格。</p></div>
        <label>操作说明
          <input value={reason} onChange={(event) => setReason(event.target.value)} placeholder="例如：v3 全部评审通过，开发环境全量启用" />
        </label>
        {run.review?.publish_allowed && run.status === "ready_for_review" && (
          <label className="style-build-confirmation">
            <input type="checkbox" checked={publishConfirmed} onChange={(event) => setPublishConfirmed(event.target.checked)} />
            我已复核全部人工接受、自动评测和待发布的精确 Bundle
          </label>
        )}
        <div className="style-action-buttons">
          {run.review?.publish_allowed && run.status === "ready_for_review" && (
            <button type="button" disabled={!publishConfirmed || action.isPending} onClick={() => action.mutate("publish")}>采纳并发布该版本</button>
          )}
          {(run.status === "evaluated" || run.status === "active") && !isActive && (
            <button type="button" disabled={!reason.trim() || action.isPending || !directActivationAllowed} onClick={activate}>全量启用该版本</button>
          )}
          {runtime?.previous_profile_version && isActive && (
            <button type="button" className="secondary" disabled={!reason.trim() || action.isPending} onClick={() => action.mutate("rollback")}>回滚到 {runtime.previous_profile_version}</button>
          )}
          {run.status === "failed_transient" && (
            <button type="button" disabled={!reason.trim() || action.isPending} onClick={() => action.mutate("retry")}>安全重试</button>
          )}
          {(PROCESSING.has(run.status) || run.status === "needs_action") && (
            <button type="button" className="danger" disabled={!reason.trim() || action.isPending} onClick={() => action.mutate("cancel")}>取消本次构建</button>
          )}
        </div>
        {!directActivationAllowed && <small>当前环境禁止页面直接全量启用，请使用 Canary 放量。</small>}
        {action.error && <p className="style-action-error">{errorText(action.error)}</p>}
      </section>

      <section className="style-build-events">
        <div className="section-heading"><div><h2>构建过程</h2><p>这里只显示阶段结果，不暴露模型思维过程。</p></div><span>{events.length} 步</span></div>
        {events.map((event) => (
          <article key={`${event.sequence}-${event.event_type}`}>
            <span>{event.sequence}</span>
            <div><strong>{event.summary}</strong><small>{event.event_type} · {formatDate(event.created_at)}</small></div>
          </article>
        ))}
      </section>

      <details className="json-view style-version-technical">
        <summary>查看产物 Hash 和模型版本</summary>
        <pre>{JSON.stringify({ hashes: run.hashes, models: run.models }, null, 2)}</pre>
      </details>
    </>
  );
}

function RuntimeOverview({ value }: { value: StyleRuntimeContext }) {
  return (
    <section className="style-runtime-overview">
      <div>
        <p className="eyebrow">RUNTIME POINTER</p>
        <h2>当前运行版本</h2>
        <strong>{value.runtime.active_profile_version}</strong>
        <small>上一版本：{value.runtime.previous_profile_version ?? "暂无"}</small>
        <small>环境回退：{value.fallback_profile_version}</small>
        <small>最后操作：{value.runtime.updated_by} · {formatDate(value.runtime.updated_at)}</small>
      </div>
      <div className="style-activation-history">
        <h3>启用 / 回滚历史</h3>
        {value.history.length === 0 && <p>暂无切换记录。</p>}
        {value.history.slice(0, 5).map((event) => (
          <article key={event.event_id}>
            <strong>{event.action === "rollback" ? "回滚" : "启用"} {event.activated_version}</strong>
            <span>来自 {event.previous_version} · revision {event.runtime_revision}</span>
            <small>{event.actor} · {formatDate(event.created_at)} · {event.reason}</small>
          </article>
        ))}
      </div>
    </section>
  );
}

function ProfileDiff({
  source,
  sourceExamples,
  candidate,
  candidateExamples,
}: {
  source: StyleProfileData | null;
  sourceExamples: StyleExampleData[];
  candidate: StyleProfileData;
  candidateExamples: StyleExampleData[];
}) {
  const newRules = useMemo(
    () => candidate.tone_rules.filter((rule) => !source?.tone_rules.includes(rule)),
    [source, candidate],
  );
  const newProhibited = candidate.prohibited_phrases.filter(
    (phrase) => !source?.prohibited_phrases.includes(phrase),
  );
  return (
    <section className="style-profile-diff">
      <div className="section-heading"><div><h2>Profile 差异</h2><p>人工要审查的是可泛化表达规则，不是某一句回复。</p></div></div>
      <div>
        <article><small>来源 · {source?.version ?? "历史素材未发布"}</small><h3>{source?.display_name ?? "—"}</h3><ul>{source?.tone_rules.map((rule) => <li key={rule}>{rule}</li>)}</ul><p>禁止短语：{source?.prohibited_phrases.join("、") || "无"}</p><ExampleList items={sourceExamples} /></article>
        <article><small>候选 · {candidate.version}</small><h3>{candidate.display_name}</h3><p>{candidate.description}</p><ul>{candidate.tone_rules.map((rule) => <li className={newRules.includes(rule) ? "new" : ""} key={rule}>{rule}</li>)}</ul><p>禁止短语：{candidate.prohibited_phrases.map((phrase) => <span className={newProhibited.includes(phrase) ? "new" : ""} key={phrase}>{phrase}　</span>)}</p><ExampleList items={candidateExamples} /></article>
      </div>
    </section>
  );
}

function ExampleList({ items }: { items: StyleExampleData[] }) {
  return (
    <details>
      <summary>查看 {items.length} 条表达示例</summary>
      <ul className="style-example-list">
        {items.map((item) => <li key={item.example_id}><strong>{item.communication_act}</strong><span>{item.text}</span></li>)}
      </ul>
    </details>
  );
}

function RegressionPanel({
  cases,
  results,
}: {
  cases: StyleRegressionCaseData[];
  results: Array<{ case_id: string; passed: boolean; failure_code?: string | null }>;
}) {
  const byCase = new Map(results.map((item) => [item.case_id, item]));
  return (
    <section className="style-regression-cases">
      <div className="section-heading"><div><h2>回归 Case 与自动评测</h2><p>基础 Case 与新增纠正派生 Case 都在这里；自动通过不代表人工采纳。</p></div><span>{cases.length} 条</span></div>
      <div>
        {cases.map((item) => {
          const result = byCase.get(item.case_id);
          return (
            <article key={item.case_id}>
              <header><strong>{item.scenario.title}</strong><span>{item.case_id.startsWith("feedback-") ? "新场景" : "基础/历史"}</span></header>
              <p>{item.scenario.response_goal}</p>
              <small>{item.response_plan.communication_act} · 自动评测 {result?.passed ? "通过" : result?.failure_code ?? "未完成"}</small>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function ClassificationPanel({ items }: { items: StyleIterationClassification[] }) {
  return (
    <section className="style-classifications">
      <div className="section-heading"><div><h2>素材分类</h2><p>十二条之外的新场景会在这里归入已有行为，或明确暂停等待处理。</p></div><span>{items.length} 条</span></div>
      <div>
        {items.map((item) => (
          <article className={!item.classification.startsWith("style_") ? "blocking" : ""} key={`${item.source_kind}-${item.source_id}`}>
            <header><strong>{CATEGORY_LABELS[item.classification] ?? item.classification}</strong><span>{item.primary_act ?? "不适用"}</span></header>
            <p>{item.summary}</p>
            {item.generalized_rule && <small>抽象规则：{item.generalized_rule}</small>}
            <code>{item.source_kind} · {item.source_id}</code>
          </article>
        ))}
      </div>
    </section>
  );
}
