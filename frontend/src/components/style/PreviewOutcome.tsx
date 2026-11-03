import type { ReviewCheck, RewriteOutcome } from "./api";

const issueLabels: Record<string, string> = {
  number_changed: "数字未保持一致",
  protected_literal_changed: "受保护的原文未保留",
  required_block_omitted: "遗漏必须表达的内容",
  profile_version_changed: "风格版本不匹配",
};

export function PreviewOutcome({ value }: { value: RewriteOutcome }) {
  if (value.passed && !value.fallback) return <p>风格改写已通过检查，语气由你确认。</p>;
  return <p role="alert">
    {value.fallback ? "当前展示的是中性原稿兜底，不是成功的风格改写。" : "本条尚未产生通过检查的风格回答。"}
    {" "}本条暂不可发布，接受兜底回答不会解除发布限制。
  </p>;
}

export function PreviewChecks({ checks }: { checks?: ReviewCheck[] }) {
  return <>{checks?.map(check => <section key={check.attempt}>
    <h4>第 {check.attempt} 次实际改写 · {check.passed ? "通过" : "未通过"}</h4>
    <p>{check.output ?? "未生成有效改写"}</p>
    <p>{check.issues.map(issue => issueLabels[issue] ? `${issueLabels[issue]}（${issue}）` : issue).join("；")}</p>
    {check.details?.filter(d => d.dimension === "expression").map((d, i) =>
      <p key={i}>表达建议（仅供参考）：{d.explanation}</p>
    )}
  </section>)}</>;
}
