import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { stylesApi, type Style, type Version } from "./api";
export const states: Record<string, string> = {
  queued: "排队中",
  building: "构建中",
  failed: "构建失败",
  ready_for_review: "待人工评审",
  published: "已发布",
};
export function BuildPanel({ style }: { style: Style }) {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ["expression-versions", style.id],
    queryFn: () => stylesApi.versions(style.id),
    refetchInterval: 4000,
  });
  const change = useMutation({
    mutationFn: ({ id, action }: { id?: string; action: string }) =>
      id ? stylesApi.action(style.id, id, action) : stylesApi.build(style.id),
    onSuccess: () => {
      void client.invalidateQueries({
        queryKey: ["expression-versions", style.id],
      });
      void client.invalidateQueries({ queryKey: ["expression-styles"] });
    },
  });
  const versions = query.data?.items ?? [];
  const active = versions.find((v) => v.id === style.active_version_id);
  return (
    <>
      <section className="expression-card">
        <h2>构建新版本</h2>
        <p>
          冻结当前风格的全部可用示例，生成表达指引、自动评测和人工评审样例。不会立即替换线上版本。
        </p>
        <div className="expression-columns">
          <div>
            <h3>当前启用</h3>
            <p>{active?.name ?? "尚未启用"}</p>
            <p>{style.example_count} 条示例素材</p>
          </div>
          {active && <GuidePreview version={active} styleId={style.id} />}
        </div>
        <button
          disabled={
            change.isPending ||
            !style.example_count ||
            versions.some((v) => ["queued", "building"].includes(v.status))
          }
          onClick={() => change.mutate({ action: "build" })}
        >
          构建新版本
        </button>
        {!style.example_count && (
          <p>先到“追加纠正素材”加入示例，再构建专属版本。</p>
        )}
      </section>
      {query.error && <p role="alert">{query.error.message}</p>}
      {change.error && <p role="alert">{change.error.message}</p>}
      {versions.map((v) => (
        <section className="expression-card" key={v.id}>
          <header>
            <h2>{v.name}</h2>
            <span>
              {v.id === style.active_version_id
                ? "当前启用"
                : (states[v.status] ?? v.status)}
            </span>
          </header>
          <p>
            冻结素材：{v.snapshot.examples?.length ?? 0} 条 · 表达样例：
            {v.examples.length} 条
          </p>
          <GuidePreview version={v} styleId={style.id} />
          {v.review_summary.total > 0 && (
            <p>
              待评审 {v.review_summary.pending} · 已接受{" "}
              {v.review_summary.accept} · 已拒绝 {v.review_summary.reject} ·
              自动检查失败 {v.review_summary.auto_failed}
            </p>
          )}
          <p>当前阶段：{v.stage}</p>
          <details>
            <summary>查看构建过程</summary>
            <ol className="expression-progress">
              {v.events.map((e, i) => (
                <li key={i}>
                  <strong>{e.stage}</strong>
                  <span>{e.message}</span>
                </li>
              ))}
            </ol>
          </details>
          {v.error && <p role="alert">{v.error}</p>}
          <div className="expression-actions">
            {v.status === "ready_for_review" && (
              <>
                <Link to={`/styles/${style.id}/review?version=${v.id}`}>
                  前往评审
                </Link>
                <button
                  disabled={
                    change.isPending ||
                    !v.review_summary.total ||
                    v.review_summary.accept !== v.review_summary.total ||
                    !!v.review_summary.auto_failed
                  }
                  onClick={() => {
                    if (
                      window.confirm(
                        `发布 ${v.name}？发布后不可修改，启用需要另行操作。`,
                      )
                    )
                      change.mutate({ id: v.id, action: "publish" });
                  }}
                >
                  采纳并发布
                </button>
                <small>全部人工接受且自动检查通过后可发布。</small>
              </>
            )}
            {v.status === "published" && v.id !== style.active_version_id && (
              <button
                disabled={change.isPending}
                onClick={() => {
                  if (window.confirm(`将 ${v.name} 设为当前风格的启用版本？`))
                    change.mutate({ id: v.id, action: "activate" });
                }}
              >
                启用此版本
              </button>
            )}
            {v.status === "failed" && (
              <button
                disabled={change.isPending}
                onClick={() => change.mutate({ id: v.id, action: "retry" })}
              >
                重试构建
              </button>
            )}
          </div>
        </section>
      ))}
    </>
  );
}
function GuidePreview({
  version,
  styleId,
}: {
  version: Version;
  styleId: string;
}) {
  return version.guide.summary ? (
    <div className="expression-guide">
      <h3>Style Guide · 表达指引</h3>
      <p>{version.guide.summary}</p>
      <ul>
        {version.guide.rules?.map((r, i) => (
          <li key={i}>
            {r.text}
            <small>
              {" "}
              ·{" "}
              {(
                {
                  stable: "稳定规则",
                  candidate: "候选规则",
                  conflict: "冲突规则",
                } as Record<string, string>
              )[r.confidence] ?? r.confidence}{" "}
              · {r.evidence_ids?.length ?? 0} 条证据
            </small>
            {r.evidence_ids?.length > 0 && (
              <details>
                <summary>查看来源示例</summary>
                {r.evidence_ids.map((id, index) => (
                  <Link
                    key={id}
                    to={`/styles/${styleId}/corrections?q=${encodeURIComponent(id)}`}
                  >
                    示例 {index + 1}{" "}
                  </Link>
                ))}
              </details>
            )}
          </li>
        ))}
      </ul>
      <small>
        只有稳定规则用于线上改写；候选和冲突规则暂不采用，可追加素材澄清后重新构建。
      </small>
      {!!version.guide.prohibited_phrases?.length && (
        <p>禁用表达：{version.guide.prohibited_phrases.join("、")}</p>
      )}
    </div>
  ) : null;
}
