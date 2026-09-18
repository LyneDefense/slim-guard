import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import type { StyleABCaseDetail, StyleABReviewInput } from "../../types";

export function StyleReviewPage() {
  const [offset, setOffset] = useState(0);
  const [selectedId, setSelectedId] = useState("");
  const cases = useQuery({ queryKey: ["style-review-cases", offset], queryFn: () => api.styleABCases(offset) });
  useEffect(() => {
    const first = cases.data?.items[0]?.case_id;
    if (!selectedId || !(cases.data?.items ?? []).some((item) => item.case_id === selectedId)) setSelectedId(first ?? "");
  }, [cases.data, selectedId]);
  return <div className="style-review-page">
    <header className="page-header"><div><p className="eyebrow">STYLE REVIEW</p><h1>评审版本</h1><p>只评审表达是否符合医生风格，不按用户意图或沟通行为分类。</p></div></header>
    {cases.isLoading && <div className="state-card">正在读取待评审样例…</div>}
    {cases.error && <div className="state-card state-error">评审样例暂不可用。</div>}
    {cases.data && <>
      <div className="style-review-summary"><Metric label="待评审" value={cases.data.items.filter((item) => !item.latest_human_review).length} /><Metric label="已评审" value={cases.data.items.filter((item) => item.latest_human_review).length} /><Metric label="本页样例" value={cases.data.items.length} /></div>
      <div className="style-review-workspace"><div className="style-review-list">{cases.data.items.map((item) => <button type="button" className={item.case_id === selectedId ? "active" : ""} onClick={() => setSelectedId(item.case_id)} key={item.case_id}><strong>{item.scenario_title}</strong><small>{item.latest_human_review ? "已评审" : "待评审"}</small></button>)}</div>{selectedId ? <ReviewCard caseId={selectedId} /> : <div className="state-card">暂无待评审样例。</div>}</div>
      <div className="pager"><button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 30))}>上一页</button><span>{cases.data.total === 0 ? 0 : offset + 1}–{Math.min(offset + 30, cases.data.total)} / {cases.data.total}</span><button type="button" disabled={offset + 30 >= cases.data.total} onClick={() => setOffset(offset + 30)}>下一页</button></div>
    </>}
  </div>;
}

function Metric({ label, value }: { label: string; value: number }) { return <article><strong>{value}</strong><span>{label}</span></article>; }

function ReviewCard({ caseId }: { caseId: string }) {
  const queryClient = useQueryClient();
  const detail = useQuery({ queryKey: ["style-review-case", caseId], queryFn: () => api.styleABCase(caseId) });
  const value = detail.data;
  const [scores, setScores] = useState({ style_match: 3, fidelity: 3, appropriateness: 3 });
  const [decision, setDecision] = useState<"accept" | "reject">("accept");
  const [comment, setComment] = useState("");
  useEffect(() => { if (value?.latest_human_review) setScores({ style_match: value.latest_human_review.style_match, fidelity: value.latest_human_review.fidelity, appropriateness: value.latest_human_review.appropriateness }); }, [value]);
  const mutation = useMutation({ mutationFn: (input: StyleABReviewInput) => api.reviewStyleABCase(caseId, input), onSuccess: async () => { setComment(""); await queryClient.invalidateQueries({ queryKey: ["style-review-cases"] }); await queryClient.invalidateQueries({ queryKey: ["style-review-case", caseId] }); } });
  if (detail.isLoading) return <div className="state-card">正在读取样例…</div>;
  if (!value) return <div className="state-card state-error">样例暂不可用。</div>;
  const current = value.candidate.response?.text ?? "—";
  const expected = value.scenario.response_goal || "请根据用户输入，保持医生风格并忠实回答。";
  const submit = () => { if (decision === "reject" && !comment.trim()) return; mutation.mutate({ ...scores, decision, comment: comment.trim(), corrects_review_id: value.latest_human_review?.review_id ?? null }); };
  return <article className="style-review-card"><p className="eyebrow">NAMED HUMAN REVIEW</p><h2>当前样例</h2><section><h3>用户输入</h3><p>{value.scenario.user_situation}</p></section><section><h3>医生回答</h3><p>{current}</p></section><section><h3>期望医生回答</h3><p>{expected}</p></section><div className="style-review-score-grid">{(["style_match", "fidelity", "appropriateness"] as const).map((key) => <label key={key}>{({ style_match: "风格匹配", fidelity: "语义忠实", appropriateness: "表达适宜" })[key]}<select value={scores[key]} onChange={(event) => setScores({ ...scores, [key]: Number(event.target.value) })}>{[1, 2, 3, 4, 5].map((score) => <option value={score} key={score}>{score} / 5</option>)}</select></label>)}</div><label>评审结论<select value={decision} onChange={(event) => setDecision(event.target.value as "accept" | "reject")}><option value="accept">接受</option><option value="reject">拒绝</option></select></label><label>{decision === "reject" ? "拒绝理由（必填）" : "评分说明（可选）"}<textarea value={comment} onChange={(event) => setComment(event.target.value)} placeholder={decision === "reject" ? "请填写拒绝理由" : "可以留空"} /></label>{mutation.error && <p className="style-ab-form-error">{mutation.error.message}</p>}<button type="button" disabled={mutation.isPending || (decision === "reject" && !comment.trim())} onClick={submit}>{mutation.isPending ? "正在保存…" : "保存评审"}</button></article>;
}
