import type { Metrics, PackageDiff, Report } from "./api";
import { materialRoles as roles, scoreDimensions } from "./labels";

export function MetricView({ value }: { value: Metrics }) {
  return (
    <>
      <div className="style-overview-grid">
        {[
          ["有效题数", value.total],
          ["候选更好", value.candidate_better],
          ["对照更好", value.baseline_better],
          ["持平", value.tie],
          ["无法确定", value.uncertain],
          ["候选检查失败", value.candidate_failed],
          ["新增关键退步", value.regressions],
          ["发生修复", value.repairs ?? 0],
          ["原稿兜底", value.fallbacks ?? 0],
        ].map(([label, count]) => (
          <article key={label}>
            <strong>{count}</strong>
            <span>{label}</span>
          </article>
        ))}
      </div>
      {value.dimensions && (
        <details>
          <summary>按维度查看评分分布（1–5 分，非客观准确率）</summary>
          <table className="style-score-table">
            <thead>
              <tr>
                <th>维度</th>
                <th>对照版 1 / 2 / 3 / 4 / 5 分</th>
                <th>候选版 1 / 2 / 3 / 4 / 5 分</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(scoreDimensions).map(([key, label]) => (
                <tr key={key}>
                  <th>{label}</th>
                  {["baseline", "candidate"].map((side) => (
                    <td key={side}>
                      {[1, 2, 3, 4, 5]
                        .map((n) => value.dimensions?.[side]?.[key]?.[n] ?? 0)
                        .join(" / ")}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </>
  );
}

export function DiffView({ value }: { value: PackageDiff }) {
  return (
    <section>
      <h3>实际产物变化</h3>
      <p>
        {value.prompt_changed ? "固定 Prompt 有变化" : "固定 Prompt 无变化"} ·
        固定示例 {value.examples_before.length} → {value.examples_after.length}
      </p>
      {value.rules_added.map((r) => (
        <p key={r.rule_id}>新增：{r.text}</p>
      ))}
      {value.rules_removed.map((r) => (
        <p key={r.rule_id}>移除：{r.text}</p>
      ))}
      {value.rules_changed.map((r) => (
        <div className="expression-columns" key={r.after.rule_id}>
          <p>
            修改前：{r.before.text}
            <br />
            {r.before.boundary}
          </p>
          <p>
            修改后：{r.after.text}
            <br />
            {r.after.boundary}
          </p>
        </div>
      ))}
    </section>
  );
}

export function ReportView({ report }: { report: Report }) {
  return (
    <>
      <h3>{report.conclusion ?? "尚无最终报告"}</h3>
      {report.metrics && <MetricView value={report.metrics} />}
      {report.difference && <DiffView value={report.difference} />}
      {!!report.regression?.total && (
        <details>
          <summary>历史拒绝用例回归（不计入独立验收）</summary>
          <MetricView value={report.regression} />
        </details>
      )}
      {!!report.feedback_outcomes?.length && (
        <details>
          <summary>人工评审反馈的实际用途</summary>
          {report.feedback_outcomes.map((r) => (
            <section key={r.example_id}>
              <p>
                {roles[r.role]} ·{" "}
                {r.used_for_rule ? "已用于 Guide" : "未作为 Guide 依据"} ·{" "}
                {r.selected_example ? "已选入固定示例" : "未选作示例"}
              </p>
              <p>{r.reason}</p>
            </section>
          ))}
        </details>
      )}
      {report.limitations?.map((text) => (
        <p key={text}>{text}</p>
      ))}
    </>
  );
}
