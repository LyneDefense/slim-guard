import type { TestCase } from "./api";

export interface FrozenSuite {
  development: TestCase[];
  acceptance: TestCase[];
}

export function TestSuiteView({ suite }: { suite: FrozenSuite }) {
  return (
    <>
      <p>
        题目先冻结再评测。最终验收结果不会用于本轮优化；以下均为合成语境，不代表执行过真实业务操作。
      </p>
      {(["development", "acceptance"] as const).map((key) => (
        <details key={key}>
          <summary>
            {key === "development" ? "开发评测题" : "独立验收题"} ·{" "}
            {suite[key].length} 条
          </summary>
          {suite[key].map((c) => (
            <article className="style-material" key={c.id}>
              <h4>{c.user_input}</h4>
              <p>已知语境：{c.context.join("；") || "无需额外语境"}</p>
              <p>中性原稿：{c.source_text}</p>
              <small>题族：{c.family}</small>
            </article>
          ))}
        </details>
      ))}
    </>
  );
}
