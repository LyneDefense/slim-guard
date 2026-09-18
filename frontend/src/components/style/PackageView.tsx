import type { Guide, StylePackage } from "./api";

export function GuideView({ guide }: { guide: Guide }) {
  return (
    <div className="expression-guide">
      <h3>表达指引</h3>
      <p>{guide.summary}</p>
      {guide.rules.map((rule) => (
        <section key={rule.rule_id}>
          <strong>{rule.text}</strong>
          <p>适用边界：{rule.boundary}</p>
          <details>
            <summary>
              {rule.evidence.length} 条支持证据 ·{" "}
              {rule.counterexample_ids.length} 条反例
            </summary>
            {rule.evidence.map((e, i) => (
              <blockquote key={i}>
                {e.quote}
                <small>
                  素材 {e.example_id} · 修订 {e.revision}
                </small>
              </blockquote>
            ))}
            {!!rule.counterexample_ids.length && (
              <p>反例素材：{rule.counterexample_ids.join("、")}</p>
            )}
          </details>
        </section>
      ))}
      {!!guide.prohibited_phrases.length && (
        <p>避免表达：{guide.prohibited_phrases.join("、")}</p>
      )}
    </div>
  );
}

export function PackageView({ value }: { value: StylePackage }) {
  return (
    <>
      <GuideView guide={value.guide} />
      <h3>固定表达示例 · {value.examples.length} 条</h3>
      {value.examples.map((e) => (
        <div className="expression-columns style-material" key={e.id}>
          <section>
            <h4>原回答</h4>
            <p>{e.original_response}</p>
          </section>
          <section>
            <h4>期望表达</h4>
            <p>{e.desired_response}</p>
          </section>
        </div>
      ))}
      <small>本版本的示例及顺序固定，不随线上输入检索切换。</small>
    </>
  );
}
