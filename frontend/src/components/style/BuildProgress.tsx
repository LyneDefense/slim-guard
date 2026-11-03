import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { stylesApi, type BuildEvent } from "./api";
import { BuildArtifact } from "./BuildArtifacts";
import { ReportView } from "./TrainingReport";

const stages: Record<string, string> = {
  freeze: "冻结全库",
  materials: "素材审查",
  construction: "提炼指引与选择示例",
  scope: "教练职责检查",
  preview: "固定用例试答",
  complete: "构建结论",
};
const states: Record<string, string> = {
  queued: "排队中",
  running: "运行中",
  failed: "失败",
  cancelled: "已取消",
  completed: "已完成",
};
const artifactLabel = (key: string) => {
  if (key.startsWith("validation:")) {
    const [, schema, identifier] = key.split(":");
    return `输出校验详情：${schema} · ${identifier.slice(0, 6)}`;
  }
  const names: Record<string, string> = {
    input_materials: "本次全部素材",
    materials: "逐条处理结果",
    suite: "测试集冻结说明",
    reference_panel: "固定人工参照",
    rounds: "每轮改动与比较",
    selected: "最终候选风格包",
    final_report: "最终报告",
    draft: "待审查风格草案",
    consistency: "一致性审查结果",
    dataset_audit: "造题审查与修复",
    preview_suite: "固定用例与样例回放",
    coach_scope: "教练职责筛选结果",
    evidence_catalog: "可用证据目录（程序绑定）",
    construction_guides: "已验证的分批／合并指引",
    retry_inputs: "重试前的回答与评审（历史保留）",
    retry_results: "本次重试候选与检查结果",
  };
  return (
    names[key] ??
    (key.startsWith("candidate:")
      ? `第 ${key.split(":")[1]} 轮候选包`
      : key.replace("evaluation:", "评测："))
  );
};
const time = (value: string | null) =>
  value ? new Date(value).toLocaleTimeString() : "尚无记录";

export function BuildProgress({
  styleId,
  runId,
}: {
  styleId: string;
  runId: string;
}) {
  const client = useQueryClient();
  const detail = useQuery({
    queryKey: ["build-detail", styleId, runId],
    queryFn: () => stylesApi.buildDetail(styleId, runId),
    refetchInterval: (query) =>
      ["queued", "running"].includes(query.state.data?.status ?? "queued")
        ? 2500
        : false,
  });
  const run = detail.data;
  const [cursor, setCursor] = useState(0);
  const [events, setEvents] = useState<BuildEvent[]>([]);
  const [selected, setSelected] = useState("input_materials");
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    setSelected(run?.job_kind === "preview_retry" ? "retry_inputs" : "input_materials");
  }, [runId, run?.job_kind]);
  const stream = useQuery({
    queryKey: [
      "build-events",
      styleId,
      runId,
      cursor,
      run?.last_event_sequence,
    ],
    queryFn: () => stylesApi.buildEvents(styleId, runId, cursor),
    enabled: !!run,
  });
  useEffect(() => {
    const chunk = stream.data;
    if (!chunk?.items.length) return;
    setEvents((previous) =>
      [
        ...new Map(
          [...previous, ...chunk.items].map((e) => [e.sequence, e]),
        ).values(),
      ].sort((a, b) => a.sequence - b.sequence),
    );
    setCursor(chunk.next_sequence);
  }, [stream.data]);
  useEffect(() => {
    if (run && !["queued", "running"].includes(run.status)) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [run?.status]);
  const action = useMutation({
    mutationFn: (value: string) => stylesApi.buildAction(styleId, runId, value),
    onSuccess: () => {
      void client.invalidateQueries({
        queryKey: ["build-detail", styleId, runId],
      });
      void client.invalidateQueries({
        queryKey: ["expression-builds", styleId],
      });
    },
  });
  if (!run)
    return (
      <p role={detail.error ? "alert" : "status"}>
        {detail.error?.message ?? "正在读取构建过程…"}
      </p>
    );
  const stale =
    run.status === "running" &&
    run.heartbeat_at &&
    now - Date.parse(run.heartbeat_at) > 90_000;
  const activity = run.activity;
  const waiting =
    activity.state === "waiting_model" && run.status === "running";
  const available = run.artifacts ?? [];
  const diagnostics = available.filter((key) => key.startsWith("validation:"));
  return (
    <section className="expression-card build-progress">
      <header>
        <h2>{run.name}</h2>
        <strong>{states[run.status] ?? run.status}</strong>
      </header>
      {run.job_kind === "preview_retry" && <p>
        只重试 {run.preview_count} 条失败用例，不修改风格包，也不重新分析素材；未重试的评审保留。
      </p>}
      <p>
        {stages[run.stage] ?? run.stage} · {activity.message}
      </p>
      {run.job_kind !== "preview_retry" && <p>
        冻结素材 {run.material_count} 条 · 模型调用 {run.usage.calls ?? 0} 次 ·
        Token {run.usage.tokens ?? 0} · 运行{" "}
        {Math.round(run.usage.seconds ?? 0)} 秒
      </p>}
      {run.job_kind === "preview_retry" && <p>
        模型调用 {run.usage.calls ?? 0} 次 · Token {run.usage.tokens ?? 0} · 运行 {Math.round(run.usage.seconds ?? 0)} 秒
      </p>}
      {run.job_kind !== "preview_retry" && <p>
        素材库 {run.library_count} 条 · 人评反馈 {run.human_feedback_count} 条 ·
        对照版本 {run.baseline_version ?? "基础表达"}
      </p>}
      <p>
        最后活动：{time(run.heartbeat_at)} · 最后产物更新：
        {time(run.progress_at)}
      </p>
      {run.job_kind !== "preview_retry" && <p>
        其中新素材／修订 {run.unused_count} 条 · 已分析 {run.analyzed_count} /{" "}
        {run.material_count} · 试答用例 {run.preview_count ?? 0} 条
      </p>}
      {waiting && (
        <p role="status">
          等待模型：{activity.purpose} · 已等待{" "}
          {Math.max(
            0,
            Math.floor(
              (now - Date.parse(activity.request_started_at ?? "")) / 1000,
            ),
          )}{" "}
          秒 （超时上限 {activity.timeout_seconds} 秒），尚无本次调用结果。
        </p>
      )}
      {(detail.error || stream.error || stale) && (
        <p role="alert">
          状态待确认：
          {detail.error?.message ??
            stream.error?.message ??
            "worker 心跳已过期"}
          。这不代表任务已经失败。
        </p>
      )}
      {run.error && (
        <p role="alert">
          {run.error}。已有阶段产物保留。{run.can_resume
            ? "可恢复本次构建；已通过的批次不重跑，失败批次会携带具体错误重新请求模型，累计执行时间不重置。"
            : run.resume_block_reason === "workflow_changed"
              ? "构建流程已更新，请新建构建；已有素材与反馈保留。"
              : "本次不能直接恢复，请根据上方具体原因处理后新建构建；已有素材与反馈保留。"}
        </p>
      )}
      <div className="expression-actions">
        {diagnostics.length > 0 && (
          <button onClick={() => setSelected(diagnostics.at(-1)!)}>
            查看输出校验详情
          </button>
        )}
        {["queued", "running"].includes(run.status) && (
          <button
            disabled={action.isPending}
            onClick={() => {
              if (
                window.confirm("取消此次构建？已完成产物会保留，不影响线上。")
              )
                action.mutate("cancel");
            }}
          >
            取消构建
          </button>
        )}
        {run.can_resume && ["failed", "cancelled"].includes(run.status) && (
          <button
            disabled={action.isPending}
            onClick={() => action.mutate("resume")}
          >
            恢复本次构建
          </button>
        )}
      </div>
      {action.error && <p role="alert">{action.error.message}</p>}
      <ol className="expression-progress">
        {Object.entries(stages).filter(([key]) => run.job_kind !== "preview_retry" || ["freeze", "preview", "complete"].includes(key)).map(([key, label]) => {
          const recorded = events.filter((e) => e.stage === key);
          const last = recorded.at(-1);
          return (
            <li key={key} className={run.stage === key ? "current" : ""}>
              <strong>{label}</strong>
              <span>{last?.message ?? "尚未执行"}</span>
              {recorded.length > 0 && (
                <details>
                  <summary>查看本阶段记录</summary>
                  {recorded
                    .filter(
                      (e) =>
                        e.state !== "waiting_model" &&
                        !e.artifact_key?.startsWith("model:") &&
                        !e.artifact_key?.startsWith("rewrite:"),
                    )
                    .map((e) => (
                      <p key={e.sequence}>
                        <small>{time(e.time)}</small> {e.message}
                      </p>
                    ))}
                </details>
              )}
            </li>
          );
        })}
      </ol>
      <label>
        查看阶段产物
        <select value={selected} onChange={(e) => setSelected(e.target.value)}>
          {available.map((key) => (
            <option key={key} value={key}>
              {artifactLabel(key)}
            </option>
          ))}
        </select>
      </label>
      {available.includes(selected) && (
        <BuildArtifact
          key={`${runId}:${selected}`}
          styleId={styleId}
          runId={runId}
          artifactKey={selected}
          revision={run.progress_at}
        />
      )}
      {run.status === "completed" && <ReportView report={run.report} />}
    </section>
  );
}
