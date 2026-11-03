import type { Review, RewriteOutcome, TestCase } from "./api";
import { PreviewChecks, PreviewOutcome } from "./PreviewOutcome";

export interface RetryInput {
  case_id: string;
  test_case: TestCase;
  automated: RewriteOutcome;
  doctor_response: string;
  review: Review | null;
  output_revision: number;
}

export interface RetryResult {
  case_id: string;
  candidate: RewriteOutcome;
}

export function RetryInputsView({ items }: { items: RetryInput[] }) {
  return <section>
    <p>这是重试开始时冻结的旧回答和旧评审，用于追溯，不代表新回答已获接受。</p>
    {items.map(item => <article className="style-material" key={item.case_id}>
      <h3>{item.test_case.user_input}</h3>
      <p>原稿：{item.test_case.source_text}</p>
      <p>重试前展示的回答：{item.doctor_response}</p>
      <PreviewOutcome value={item.automated} />
      <p>旧回答修订 {item.output_revision} · 旧评审：{item.review?.decision === "accept" ? "已接受" : item.review ? "已拒绝" : "待评审"}</p>
      {item.review?.reason && <p>原评审说明：{item.review.reason}</p>}
      <details><summary>查看旧改写与失败原因</summary><PreviewChecks checks={item.automated.checks} /></details>
    </article>)}
  </section>;
}

export function RetryResultsView({ items }: { items: RetryResult[] }) {
  return <section>
    <p>以下为本次候选；任务完整完成后才统一更新目标版本。取消或执行失败不会覆盖原回答。</p>
    {items.map(item => <article className="style-material" key={item.case_id}>
      <p>{item.candidate.text}</p>
      <PreviewOutcome value={item.candidate} />
      <PreviewChecks checks={item.candidate.checks} />
    </article>)}
  </section>;
}
