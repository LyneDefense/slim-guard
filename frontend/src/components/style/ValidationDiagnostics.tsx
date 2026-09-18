export interface ValidationDiagnostic {
  schema_name: string;
  status: "unresolved" | "repaired";
  max_attempts_per_execution: number;
  failures: Array<{
    number: number;
    time: string;
    execution_attempt: number;
    raw_output: string | null;
    tool_calls: unknown[];
    finish_reason: string | null;
    errors: Array<{ path: string; type: string; message: string }>;
  }>;
}

export function ValidationDiagnostics({
  value,
}: {
  value: ValidationDiagnostic;
}) {
  return (
    <section className="validation-diagnostics">
      <h3>结构校验详情 · {value.schema_name}</h3>
      <p>
        {value.status === "repaired" ? "已修复，失败记录仍保留" : "尚未修复"}
        。单次执行最多尝试 {value.max_attempts_per_execution} 次（含首次请求）；
        手动恢复仍沿用原任务剩余预算和上次错误。
      </p>
      {value.failures.map((failure) => (
        <article className="style-material" key={failure.number}>
          <h4>
            失败记录 {failure.number} · 本次执行第 {failure.execution_attempt} 次尝试
          </h4>
          <p>
            {new Date(failure.time).toLocaleString()} · 模型结束原因：
            {failure.finish_reason ?? "未提供"}
          </p>
          <ul>
            {failure.errors.map((error, index) => (
              <li key={index}>
                <code>{error.path}</code>：{error.message}（{error.type}）
              </li>
            ))}
          </ul>
          <details>
            <summary>查看模型原始输出（未通过校验）</summary>
            <pre>{failure.raw_output || "模型未返回文本内容"}</pre>
          </details>
          {failure.tool_calls.length > 0 && (
            <details>
              <summary>查看被拒绝的工具调用（未执行）</summary>
              <pre>{JSON.stringify(failure.tool_calls, null, 2)}</pre>
            </details>
          )}
        </article>
      ))}
    </section>
  );
}
