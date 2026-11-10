import { diagnosticText, type StudioCheck, type StyleExample } from "../model";
export function CheckResult({
  result,
  examples = [],
}: {
  result: StudioCheck;
  examples?: StyleExample[];
}) {
  const passedCount = result.checks.filter(
    (check) => check.role_passed && check.safety_passed,
  ).length;
  const sourceLabel = (source: string) => {
    if (source === "persona") return "角色设定";
    const index = examples.findIndex(
      (example) => `example:${example.id}` === source,
    );
    return index >= 0
      ? examples[index].title || `示例 ${index + 1}`
      : "对话示例";
  };
  return (
    <section
      className={`studio-check ${result.passed ? "studio-check-good" : "studio-check-bad"}`}
      aria-label="必要检查结果"
    >
      <strong>{result.passed ? "必要检查已通过" : "必要检查未通过"}</strong>
      <p>
        已通过 {passedCount} / {result.checks.length}{" "}
        项职责与安全检查。语气是否合意，请结合试聊判断。
      </p>
      {result.checks
        .filter((check) => !check.role_passed || !check.safety_passed)
        .map((check) => (
          <div className="studio-check-issue" key={check.source_id}>
            <strong>{sourceLabel(check.source_id)}</strong>
            <ul>
              {check.issues.map((issue, i) => (
                <li key={i}>
                  {issue.dimension === "role" ? "职责边界" : "安全边界"}：
                  {issue.explanation}
                </li>
              ))}
            </ul>
          </div>
        ))}
      {result.warnings.length > 0 && (
        <ul>
          {result.warnings.map((warning, i) => (
            <li key={i}>{warning}</li>
          ))}
        </ul>
      )}
      <details>
        <summary>详细诊断</summary>
        <pre>{diagnosticText(result.checks)}</pre>
      </details>
    </section>
  );
}
