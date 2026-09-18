import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  stylesApi,
  type StylePackage,
  type Report,
  type EvaluationRow,
  type MaterialInput,
  type MaterialResult,
  type Round,
} from "./api";
import { materialRoles as roles } from "./labels";
import { TestSuiteView, type FrozenSuite } from "./TestSuiteView";
import { PackageView } from "./PackageView";
import { ReportView, MetricView, DiffView } from "./TrainingReport";
import {
  ValidationDiagnostics,
  type ValidationDiagnostic,
} from "./ValidationDiagnostics";

function EvaluationView({ row }: { row: EvaluationRow }) {
  return (
    <article className="style-material">
      <h3>{row.case.user_input}</h3>
      <p>语境：{row.case.context.join("；") || "无需额外语境"}</p>
      <div className="expression-three">
        <section>
          <h4>中性原稿</h4>
          <p>{row.case.source_text}</p>
        </section>
        <section>
          <h4>对照回答</h4>
          <p>{row.baseline.text}</p>
        </section>
        <section>
          <h4>候选回答</h4>
          <p>{row.candidate.text}</p>
        </section>
      </div>
      <p>
        {row.candidate.passed ? "共享审查通过" : "检查失败／原稿兜底"} ·{" "}
        {row.reason}
      </p>
      {!row.candidate.passed && (
        <p role="alert">{row.candidate.failure_code ?? "检查未完成"}</p>
      )}
      <details>
        <summary>查看审查与修复过程</summary>
        {row.candidate.checks?.map((c) => (
          <section key={c.attempt}>
            <h4>
              第 {c.attempt} 次改写 · {c.passed ? "通过" : "未通过"}
            </h4>
            <p>{c.output ?? "未生成有效输出"}</p>
            <p>{c.issues.join("；")}</p>
          </section>
        ))}
        <p>
          最终处理：
          {row.candidate.fallback
            ? "使用中性原稿兜底"
            : row.candidate.passed
              ? "采用改写"
              : "不满足验收条件"}
        </p>
      </details>
    </article>
  );
}

export function BuildArtifact({
  styleId,
  runId,
  artifactKey,
  revision,
}: {
  styleId: string;
  runId: string;
  artifactKey: string;
  revision?: string | null;
}) {
  const [offset, setOffset] = useState(0);
  const query = useQuery({
    queryKey: ["build-artifact", styleId, runId, artifactKey, offset, revision],
    queryFn: () => stylesApi.artifact(styleId, runId, artifactKey, offset),
  });
  if (query.isLoading) return <p>正在读取阶段产物…</p>;
  if (query.error) return <p role="alert">{query.error.message}</p>;
  const data = query.data;
  if (!data) return null;
  return (
    <div className="build-artifact">
      {artifactKey.startsWith("validation:") && data.value != null && (
        <ValidationDiagnostics value={data.value as ValidationDiagnostic} />
      )}
      {(artifactKey.startsWith("candidate:") ||
        artifactKey === "selected" ||
        artifactKey === "draft") &&
        data.value != null && (
          <PackageView value={data.value as StylePackage} />
        )}
      {artifactKey === "final_report" && data.value != null && (
        <ReportView report={data.value as Report} />
      )}
      {artifactKey === "suite" && data.value != null && (
        <TestSuiteView suite={data.value as FrozenSuite} />
      )}
      {artifactKey === "dataset_audit" && data.value != null && (
        <DatasetAudit
          value={
            data.value as {
              attempt: number;
              passed: boolean;
              issues: string[];
              suite: FrozenSuite;
            }
          }
        />
      )}
      {artifactKey.startsWith("evaluation:") &&
        (data.items as EvaluationRow[] | undefined)?.map((r) => (
          <EvaluationView key={r.case.id} row={r} />
        ))}
      {artifactKey === "materials" &&
        (data.items as (MaterialResult & MaterialInput)[] | undefined)?.map(
          (r) => (
            <article key={r.example_id} className="style-material">
              <strong>
                {roles[r.role] ?? r.role} · {r.user_input}
              </strong>
              <p>{r.reason}</p>
              <p>{r.expression_rule}</p>
              <details>
                <summary>查看原始素材</summary>
                <p>原回答：{r.original_response}</p>
                <p>期望：{r.desired_response || "未提供"}</p>
                <p>意见：{r.correction_opinion}</p>
              </details>
              <small>素材 {r.example_id}</small>
            </article>
          ),
        )}
      {artifactKey === "consistency" &&
        (
          data.items as
            | Array<{ batch: number; passed: boolean; issues: string[] }>
            | undefined
        )?.map((r) => (
          <section key={r.batch}>
            <h4>
              第 {r.batch} 批 ·{" "}
              {r.passed && !r.issues.length ? "一致性通过" : "未通过"}
            </h4>
            <p>{r.issues.join("；")}</p>
          </section>
        ))}
      {artifactKey === "input_materials" &&
        (data.items as MaterialInput[] | undefined)?.map((m, i) => (
          <article className="style-material" key={i}>
            <h4>{m.user_input}</h4>
            <p>原回答：{m.original_response}</p>
            <p>期望回答：{m.desired_response || "未提供"}</p>
            <p>纠正意见：{m.correction_opinion || "未提供"}</p>
          </article>
        ))}
      {artifactKey === "rounds" &&
        (data.items as Round[] | undefined)?.map((r) => (
          <section key={r.round} className="style-material">
            <h3>
              第 {r.round} 轮 · {r.retained ? "保留候选" : "未保留"}
            </h3>
            <p>{r.explanation}</p>
            <MetricView value={r.metrics} />
            <DiffView value={r.difference} />
          </section>
        ))}
      {artifactKey === "reference_panel" &&
        (
          data.items as
            | Array<{
                id: string;
                original: string;
                desired: string;
                opinion: string;
              }>
            | undefined
        )?.map((r) => (
          <article className="style-material" key={r.id}>
            <p>原回答：{r.original}</p>
            <p>人工期望：{r.desired}</p>
            <p>{r.opinion}</p>
          </article>
        ))}
      {data.items && (
        <div className="pager">
          <button
            disabled={!offset}
            onClick={() => setOffset(Math.max(0, offset - 20))}
          >
            上一页
          </button>
          <span>共 {data.total} 条</span>
          <button
            disabled={offset + 20 >= (data.total ?? 0)}
            onClick={() => setOffset(offset + 20)}
          >
            下一页
          </button>
        </div>
      )}
    </div>
  );
}

function DatasetAudit({
  value,
}: {
  value: {
    attempt: number;
    passed: boolean;
    issues: string[];
    suite: FrozenSuite;
  };
}) {
  return (
    <section>
      <h3>
        第 {value.attempt} 次造题检查 · {value.passed ? "通过" : "需修复"}
      </h3>
      <p>{value.issues.join("；")}</p>
      <TestSuiteView suite={value.suite} />
    </section>
  );
}
