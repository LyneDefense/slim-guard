import type { ReviewCheck, RewriteOutcome } from "./api";

const issueLabels: Record<string, string> = {
  number_changed: "数字未保持一致",
  protected_literal_changed: "受保护的原文未保留",
  required_block_omitted: "遗漏必须表达的内容",
  profile_version_changed: "风格版本不匹配",
};

export function PreviewOutcome({ value }: { value: RewriteOutcome }) {
  if (value.passed && !value.fallback) return <p>
    医生回答已通过事实与职责检查，语气由你确认。
    {value.style_advisory && "仍有表达建议，已保留审查通过的回答，不因风格偏差隐藏医生回复。"}
  </p>;
  return <p role="alert">
    {value.mode === "direct_generation" ? "本条未产生通过事实与职责检查的医生回答，不以中性稿冒充成功。"
      : value.fallback ? "旧版中性原稿兜底，不是成功的医生直接试答。" : "本条尚未产生通过检查的回答。"}
    {" "}本条暂不可发布，接受兜底回答不会解除发布限制。
  </p>;
}

export function PreviewChecks({ checks }: { checks?: ReviewCheck[] }) {
  return <>{checks?.map(check => <section key={`${check.step ?? 1}:${check.attempt}`}>
    <h4>{check.step ? `第 ${check.step} 轮 · ` : ""}第 {check.attempt} 次试答 · {check.passed ? "事实与职责通过" : "未通过"}</h4>
    {check.adopted && <small>本轮已采用</small>}
    <p>{check.output ?? "未生成有效改写"}</p>
    <p>{check.issues.map(issue => issueLabels[issue] ? `${issueLabels[issue]}（${issue}）` : issue).join("；")}</p>
    {check.details?.filter(d => d.dimension === "expression").map((d, i) =>
      <p key={i}>表达建议（仅供参考）：{d.explanation}</p>
    )}
  </section>)}</>;
}
