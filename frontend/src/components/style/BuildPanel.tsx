import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { stylesApi, type Style } from "./api";
import { BuildProgress } from "./BuildProgress";
import { PackageView } from "./PackageView";
import { ReportView } from "./TrainingReport";

export function BuildPanel({ style }: { style: Style }) {
  const client = useQueryClient();
  const [params] = useSearchParams();
  const [selected, setSelected] = useState(params.get("run") ?? "");
  const [offset, setOffset] = useState(0);
  const versions = useQuery({
    queryKey: ["expression-versions", style.id],
    queryFn: () => stylesApi.versions(style.id),
    refetchInterval: 5000,
  });
  const builds = useQuery({
    queryKey: ["expression-builds", style.id, offset],
    queryFn: () => stylesApi.builds(style.id, offset),
    refetchInterval: 2500,
  });
  const refresh = () => {
    void client.invalidateQueries({
      queryKey: ["expression-versions", style.id],
    });
    void client.invalidateQueries({
      queryKey: ["expression-builds", style.id],
    });
    void client.invalidateQueries({ queryKey: ["expression-styles"] });
    void client.invalidateQueries({
      queryKey: ["expression-examples", style.id],
    });
  };
  const start = useMutation({
    mutationFn: () => stylesApi.build(style.id, crypto.randomUUID()),
    onSuccess: (run) => {
      setSelected(run.id);
      setOffset(0);
      refresh();
    },
  });
  const action = useMutation({
    mutationFn: ({ id, action }: { id: string; action: string }) =>
      stylesApi.action(style.id, id, action),
    onSuccess: refresh,
  });
  const active = versions.data?.items.find(
    (v) => v.id === style.active_version_id,
  );
  const runs = builds.data?.items ?? [];
  const current = selected || runs[0]?.id;
  return (
    <>
      <section className="expression-card">
        <h2>构建新版本</h2>
        <p>
          全库素材参与筛选，联合构建表达指引与固定示例，在独立测试上比较效果。完成后仍需人评、发布和启用。
        </p>
        <p>
          当前启用：{active?.name ?? "尚未启用"} · 素材 {style.example_count} 条
        </p>
        {active && (
          <details>
            <summary>查看当前风格包</summary>
            <PackageView value={active.package} />
          </details>
        )}
        <button
          disabled={
            start.isPending ||
            !style.example_count ||
            runs.some((r) => ["queued", "running"].includes(r.status))
          }
          onClick={() => start.mutate()}
        >
          构建新版本
        </button>
        {!style.example_count && <p>先到“追加纠正素材”手工加入素材。</p>}
        {start.error && <p role="alert">{start.error.message}</p>}
      </section>
      <section className="expression-card">
        <h2>训练过程</h2>
        <label>
          查看本次或历史构建
          <select
            value={current ?? ""}
            onChange={(e) => setSelected(e.target.value)}
          >
            {runs.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
        </label>
        {builds.error && (
          <p role="alert">无法更新构建列表：{builds.error.message}</p>
        )}
        {!runs.length && !builds.isLoading && <p>暂无构建任务。</p>}
        {builds.data && builds.data.total > 20 && (
          <div className="pager">
            <button
              disabled={!offset}
              onClick={() => {
                setSelected("");
                setOffset(Math.max(0, offset - 20));
              }}
            >
              上一页
            </button>
            <span>共 {builds.data.total} 次构建</span>
            <button
              disabled={offset + 20 >= builds.data.total}
              onClick={() => {
                setSelected("");
                setOffset(offset + 20);
              }}
            >
              下一页
            </button>
          </div>
        )}
      </section>
      {current && (
        <BuildProgress key={current} styleId={style.id} runId={current} />
      )}
      <h2>最终风格版本</h2>
      <p>这里不展示自动优化的中间候选。</p>
      {versions.error && <p role="alert">{versions.error.message}</p>}
      {action.error && <p role="alert">{action.error.message}</p>}
      {versions.data?.items.map((v) => (
        <section className="expression-card" key={v.id}>
          <header>
            <h2>{v.name}</h2>
            <span>
              {v.id === style.active_version_id
                ? "当前启用"
                : v.status === "published"
                  ? "已发布"
                  : "待人工评审"}
            </span>
          </header>
          {v.report.conclusion && <ReportView report={v.report} />}
          <details>
            <summary>查看 Guide 与固定示例</summary>
            <PackageView value={v.package} />
          </details>
          {!!v.review_summary.total && (
            <p>
              待评审 {v.review_summary.pending} · 接受 {v.review_summary.accept}{" "}
              · 拒绝 {v.review_summary.reject} · 自动检查失败{" "}
              {v.review_summary.auto_failed}
            </p>
          )}
          <div className="expression-actions">
            {v.build_run_id && (
              <button onClick={() => setSelected(v.build_run_id ?? "")}>
                查看本版本训练过程
              </button>
            )}
            {v.status === "ready_for_review" && (
              <>
                <Link to={`/styles/${style.id}/review?version=${v.id}`}>
                  前往评审
                </Link>
                <button
                  disabled={
                    action.isPending ||
                    !v.report.release_eligible ||
                    !v.review_summary.total ||
                    v.review_summary.accept !== v.review_summary.total ||
                    !!v.review_summary.auto_failed
                  }
                  onClick={() => {
                    if (
                      window.confirm(
                        `发布 ${v.name}？发布后不可修改，启用另行操作。`,
                      )
                    )
                      action.mutate({ id: v.id, action: "publish" });
                  }}
                >
                  采纳并发布
                </button>
                <small>独立验收及回归检查通过、人工全部接受后可发布。</small>
              </>
            )}
            {v.status === "published" && v.id !== style.active_version_id && (
              <button
                disabled={action.isPending}
                onClick={() => {
                  if (window.confirm(`启用 ${v.name}？将影响后续的新回复。`))
                    action.mutate({ id: v.id, action: "activate" });
                }}
              >
                启用此版本
              </button>
            )}
          </div>
        </section>
      ))}
    </>
  );
}
