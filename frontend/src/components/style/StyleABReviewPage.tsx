import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";

import { api } from "../../api";
import type {
  StyleABCaseDetail,
  StyleABCaseSummary,
  StyleABReviewInput,
  StyleABSide,
  StyleABStatistics,
} from "../../types";

const ACT_LABELS: Record<string, string> = {
  acknowledge: "确认",
  correct: "纠正",
  remind: "提醒",
  encourage: "鼓励",
  explain: "解释",
  ask: "询问",
};

function displayHash(value: string): string {
  return value || "—";
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

export function StyleABReviewPage() {
  const [searchParams] = useSearchParams();
  const [offset, setOffset] = useState(0);
  const [profile, setProfile] = useState(searchParams.get("version") ?? "");
  const [act, setAct] = useState("");
  const [decision, setDecision] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const context = useQuery({
    queryKey: ["style-ab-context"],
    queryFn: api.styleABContext,
  });
  const filters = {
    candidate_profile_version: profile || undefined,
    communication_act: act || undefined,
    decision: decision || undefined,
  };
  const cases = useQuery({
    queryKey: ["style-ab-cases", offset, profile, act, decision],
    queryFn: () => api.styleABCases(offset, filters),
  });
  const statistics = useQuery({
    queryKey: ["style-ab-statistics", profile],
    queryFn: () => api.styleABStatistics(profile),
  });
  useEffect(() => {
    const items = cases.data?.items ?? [];
    if (items.length > 0 && !items.some((item) => item.case_id === selectedId)) {
      setSelectedId(items[0].case_id);
    } else if (items.length === 0) {
      setSelectedId("");
    }
  }, [cases.data, selectedId]);

  return (
    <div className="page style-ab-page">
      <header className="page-header">
        <div>
          <p className="eyebrow">STYLE A/B · HUMAN REVIEW</p>
          <h1>表达风格人工评分</h1>
          <p>仅比较已脱敏的合成业务计划与两种表达输出；自动 Judge 与实名人评分分开记录。</p>
        </div>
      </header>
      {statistics.data && <StyleABStats value={statistics.data} />}
      <StyleABFilters
        profile={profile}
        versions={context.data?.candidate_profile_versions ?? []}
        act={act}
        decision={decision}
        onChange={(next) => {
          setOffset(0);
          setProfile(next.profile);
          setAct(next.act);
          setDecision(next.decision);
        }}
      />
      {cases.isLoading && <div className="state-card">正在读取 A/B 评估对…</div>}
      {cases.error && <div className="state-card state-error">A/B 评估对暂不可用。</div>}
      {cases.data && (
        <div className="style-ab-workspace">
          <StyleABCaseList
            items={cases.data.items}
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
          {selectedId
            ? <StyleABCaseReview key={selectedId} caseId={selectedId} />
            : <div className="state-card">当前筛选条件下没有待展示评估对。</div>}
        </div>
      )}
      {cases.data && (
        <div className="pager">
          <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 30))}>上一页</button>
          <span>{cases.data.total === 0 ? 0 : offset + 1}–{Math.min(offset + 30, cases.data.total)} / {cases.data.total}</span>
          <button disabled={offset + 30 >= cases.data.total} onClick={() => setOffset(offset + 30)}>下一页</button>
        </div>
      )}
    </div>
  );
}

function StyleABStats({ value }: { value: StyleABStatistics }) {
  const average = (key: string) => {
    const score = value.scores[key]?.average;
    return score == null ? "未评分" : `${score.toFixed(2)} / 5`;
  };
  const acceptance = value.denominators.acceptance_rate > 0
    ? `${(value.rates.acceptance_rate * 100).toFixed(1)}%`
    : "未评分";
  return (
    <section className="style-ab-stats" aria-label="最新人工评分统计">
      <div className="metric"><strong>{value.counts.reviewed_case_count}</strong><span>已评分 Case</span></div>
      <div className="metric"><strong>{value.counts.pending_case_count}</strong><span>待评分 Case</span></div>
      <div className="metric"><strong>{value.counts.accepted_case_count}</strong><span>最新接受</span></div>
      <div className="metric"><strong>{value.counts.rejected_case_count}</strong><span>最新拒绝</span></div>
      <div className="metric"><strong>{acceptance}</strong><span>接受率（分母 {value.denominators.acceptance_rate}）</span></div>
      <div className="metric"><strong>{average("style_match")}</strong><span>风格匹配均分</span></div>
      <div className="metric"><strong>{average("fidelity")}</strong><span>语义忠实均分</span></div>
      <div className="metric"><strong>{average("appropriateness")}</strong><span>表达适宜均分</span></div>
      <p>统计按每个 Case 的最新一条实名评分计算；更正会新增记录，不覆盖历史。</p>
    </section>
  );
}

export function StyleABFilters({
  profile,
  versions,
  act,
  decision,
  onChange,
}: {
  profile: string;
  versions: string[];
  act: string;
  decision: string;
  onChange: (value: { profile: string; act: string; decision: string }) => void;
}) {
  return (
    <div className="filters style-ab-filters">
      <label>候选版本
        <select
          aria-label="候选版本"
          value={profile}
          onChange={(event) => onChange({ profile: event.target.value, act, decision })}
        >
          <option value="">全部版本</option>
          {versions.map((version) => (
            <option key={version} value={version}>{version}</option>
          ))}
        </select>
      </label>
      <label>沟通行为
        <select value={act} onChange={(event) => onChange({ profile, act: event.target.value, decision })}>
          <option value="">全部</option>
          {Object.entries(ACT_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <label>人工结论
        <select value={decision} onChange={(event) => onChange({ profile, act, decision: event.target.value })}>
          <option value="">全部</option>
          <option value="pending">待评分</option>
          <option value="accept">接受</option>
          <option value="reject">拒绝</option>
        </select>
      </label>
    </div>
  );
}

function StyleABCaseList({
  items,
  selectedId,
  onSelect,
}: {
  items: StyleABCaseSummary[];
  selectedId: string;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="style-ab-case-list" aria-label="A/B 评估 Case">
      {items.map((item) => (
        <button
          type="button"
          className={item.case_id === selectedId ? "active" : ""}
          onClick={() => onSelect(item.case_id)}
          key={item.case_id}
        >
          <strong>{ACT_LABELS[item.communication_act] ?? item.communication_act}</strong>
          <em>{item.scenario_title}</em>
          <span>{item.candidate.profile_version}</span>
          <small>
            {item.latest_human_review
              ? `${item.latest_human_review.decision === "accept" ? "接受" : "拒绝"} · ${item.latest_human_review.actor}`
              : "待实名评分"}
          </small>
        </button>
      ))}
    </div>
  );
}

function StyleABCaseReview({ caseId }: { caseId: string }) {
  const detail = useQuery({
    queryKey: ["style-ab-case", caseId],
    queryFn: () => api.styleABCase(caseId),
    enabled: Boolean(caseId),
  });
  if (detail.isLoading) return <div className="state-card">正在读取评估对…</div>;
  if (detail.error || !detail.data) {
    return <div className="state-card state-error">评估对暂不可用。</div>;
  }
  return (
    <div>
      <StyleABCaseData value={detail.data} />
      <StyleABReviewForm
        key={`${detail.data.case_id}:${detail.data.latest_human_review?.review_id ?? "first"}`}
        value={detail.data}
      />
    </div>
  );
}

export function StyleABCaseData({ value }: { value: StyleABCaseDetail }) {
  const blocks = value.response_plan.content_blocks ?? [];
  return (
    <section className="style-ab-detail">
      <header>
        <div>
          <p className="eyebrow">SYNTHETIC CASE · {value.case_key}</p>
          <h2>{ACT_LABELS[value.communication_act] ?? value.communication_act}</h2>
        </div>
        <span>{value.latest_human_review ? "已有实名评分" : "待实名评分"}</span>
      </header>
      <div className="style-ab-provenance">
        <span>样本 <code>{displayHash(value.source_sample_sha256)}</code></span>
        <span>场景 <code>{displayHash(value.scenario_sha256)}</code></span>
        <span>计划 <code>{displayHash(value.response_plan_sha256)}</code></span>
        <span>Bundle <code>{displayHash(value.candidate_bundle_sha256)}</code></span>
      </div>
      <article className="style-ab-scenario">
        <p className="eyebrow">REVIEW SCENARIO · 合成审核语境</p>
        <h3>{value.scenario.title}</h3>
        <dl>
          <div><dt>用户刚刚发生了什么</dt><dd>{value.scenario.user_situation}</dd></div>
          <div>
            <dt>系统已确认的上下文</dt>
            <dd><ul>{value.scenario.known_context.map((item) => <li key={item}>{item}</li>)}</ul></dd>
          </div>
          <div><dt>这条回复要完成什么</dt><dd>{value.scenario.response_goal}</dd></div>
        </dl>
        <small>这是专门用于评估的合成场景，不是微信群聊原文，也不代表新增用户事实。</small>
      </article>
      <article className="style-ab-plan">
        <h3>同一份合成 ResponsePlan</h3>
        {blocks.map((block) => (
          <p key={block.block_id ?? JSON.stringify(block)}>
            <strong>{block.kind ?? "内容"}</strong>{block.text ?? "（无文本）"}
          </p>
        ))}
      </article>
      <div className="style-ab-outputs">
        <StyleABOutput title="Baseline" side={value.baseline} />
        <StyleABOutput title="Candidate" side={value.candidate} />
      </div>
      <article className="style-ab-judge">
        <div>
          <p className="eyebrow">AUTOMATED JUDGE · 独立记录</p>
          <strong>{value.automated_judge.status}</strong>
        </div>
        <dl>
          <div><dt>Judge 模型</dt><dd>{value.automated_judge.model}</dd></div>
          <div><dt>结果 Hash</dt><dd><code>{displayHash(value.automated_judge.evaluation_sha256)}</code></dd></div>
        </dl>
        <small>自动结果不是人工评分，也不会冒充人工审批。</small>
      </article>
      {value.reviews.length > 0 && (
        <details className="style-ab-history">
          <summary>查看 {value.reviews.length} 条 append-only 人工评分历史</summary>
          {value.reviews.map((review) => (
            <article key={review.review_id}>
              <strong>{review.actor} · {review.decision === "accept" ? "接受" : "拒绝"}</strong>
              <span>风格 {review.style_match} · 忠实 {review.fidelity} · 适宜 {review.appropriateness}</span>
              <p>{review.comment || "未填写评分说明"}</p>
              <small>{formatDate(review.created_at)}</small>
            </article>
          ))}
        </details>
      )}
    </section>
  );
}

function StyleABOutput({ title, side }: { title: string; side: StyleABSide }) {
  return (
    <article>
      <header><strong>{title}</strong><code>{side.profile_version}</code></header>
      <p>{side.response?.text ?? "响应正文未记录"}</p>
      <dl>
        <div><dt>生成模型</dt><dd>{side.generation_model}</dd></div>
        <div><dt>响应 Hash</dt><dd><code>{displayHash(side.response_sha256)}</code></dd></div>
      </dl>
      <small>
        表达示例 ID：{side.example_ids.length > 0 ? side.example_ids.join("、") : "未使用"}
      </small>
    </article>
  );
}

function StyleABReviewForm({ value }: { value: StyleABCaseDetail }) {
  const queryClient = useQueryClient();
  const latest = value.latest_human_review;
  const [styleMatch, setStyleMatch] = useState(() => latest?.style_match ?? 3);
  const [fidelity, setFidelity] = useState(() => latest?.fidelity ?? 3);
  const [appropriateness, setAppropriateness] = useState(
    () => latest?.appropriateness ?? 3,
  );
  const [decision, setDecision] = useState<"accept" | "reject">(
    () => latest?.decision ?? "accept",
  );
  const [comment, setComment] = useState("");
  const commentRequired = decision === "reject";

  const mutation = useMutation({
    mutationFn: (input: StyleABReviewInput) => api.reviewStyleABCase(value.case_id, input),
    onSuccess: async () => {
      setComment("");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["style-ab-case", value.case_id] }),
        queryClient.invalidateQueries({ queryKey: ["style-ab-cases"] }),
        queryClient.invalidateQueries({ queryKey: ["style-ab-statistics"] }),
      ]);
    },
  });
  const submit = () => {
    if (commentRequired && !comment.trim()) return;
    mutation.mutate({
      style_match: styleMatch,
      fidelity,
      appropriateness,
      decision,
      comment: comment.trim(),
      corrects_review_id: value.latest_human_review?.review_id ?? null,
    });
  };

  return (
    <section className="style-ab-review-form">
      <header>
        <div>
          <p className="eyebrow">NAMED HUMAN REVIEW</p>
          <h3>{value.latest_human_review ? "新增更正评分" : "提交实名评分"}</h3>
        </div>
        {value.latest_human_review && <span>不会覆盖 {value.latest_human_review.actor} 的历史记录</span>}
      </header>
      <div className="style-ab-score-grid">
        <ScoreSelect label="风格匹配" value={styleMatch} onChange={setStyleMatch} />
        <ScoreSelect label="语义忠实" value={fidelity} onChange={setFidelity} />
        <ScoreSelect label="表达适宜" value={appropriateness} onChange={setAppropriateness} />
      </div>
      <label>人工结论
        <select value={decision} onChange={(event) => setDecision(event.target.value as "accept" | "reject")}>
          <option value="accept">接受</option>
          <option value="reject">拒绝</option>
        </select>
      </label>
      <label>{commentRequired ? "评分说明（拒绝时必填）" : "评分说明（选填）"}
        <textarea
          value={comment}
          maxLength={2000}
          required={commentRequired}
          onChange={(event) => setComment(event.target.value)}
          placeholder={commentRequired
            ? "请说明拒绝原因，并给出你期望的表达；不要粘贴原微信群聊。"
            : "接受时可不填写；也可以补充评价。"}
        />
      </label>
      {mutation.error && <p className="style-ab-form-error">{mutation.error.message}</p>}
      {mutation.isSuccess && <p className="style-ab-form-success">实名评分已追加保存。</p>}
      <button
        type="button"
        disabled={mutation.isPending || (commentRequired && !comment.trim())}
        onClick={submit}
      >
        {mutation.isPending ? "正在保存…" : value.latest_human_review ? "追加更正" : "提交评分"}
      </button>
      <small>评分账号来自当前已鉴权管理员会话，页面不允许自行填写或冒充 actor。</small>
    </section>
  );
}

function ScoreSelect({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <label>{label}
      <select value={value} onChange={(event) => onChange(Number(event.target.value))}>
        {[1, 2, 3, 4, 5].map((score) => <option value={score} key={score}>{score} / 5</option>)}
      </select>
    </label>
  );
}
