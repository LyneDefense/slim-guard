import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { callApi } from "./api";
import {
  Badge,
  CallState,
  FinalOutput,
  Flow,
  Inspector,
  Json,
  Waterfall,
} from "./components";
import {
  date,
  elapsed,
  needsRefresh,
  object,
  problemNode,
} from "./presentation";
import type { CallDetail } from "./types";
import { Workspace } from "../../app/Workspace";
import { callListReturn } from "./navigation";
import "./calls.css";

export function CallDetailPage() {
  const { userId = "", traceId = "" } = useParams();
  const query = useQuery({
    queryKey: ["call-detail", userId, traceId],
    queryFn: () => callApi.detail(userId, traceId),
    enabled: !!userId && !!traceId,
    refetchInterval: (state) => (needsRefresh(state.state.data) ? 3000 : false),
  });
  return (
    <Workspace
      className="call-workspace"
      title="调用过程 / 单次详情"
      note={
        <>
          {needsRefresh(query.data) ? "每 3 秒更新" : "处理已结束"} ·
          敏感读取已审计
        </>
      }
    >
      <div className="call-main">
        {query.isPending && <CallState loading />}
        {query.isError && (
          <CallState
            error={query.error}
            retry={() => {
              void query.refetch();
            }}
          />
        )}
        {query.data && <CallDetailView key={traceId} data={query.data} />}
      </div>
    </Workspace>
  );
}

export function CallDetailView({ data }: { data: CallDetail }) {
  const [params, setParams] = useSearchParams();
  const [expanded, setExpanded] = useState(true);
  const process = data.process,
    stats = process.summary;
  const problem = problemNode(process.nodes);
  const selected =
    process.nodes.find((n) => n.id === params.get("step")) ||
    problem ||
    process.nodes.find(
      (n) =>
        n.kind === "invocation" &&
        object(n.raw).agent_role === "coach_response",
    ) ||
    process.nodes[0];
  const mode = params.get("view") === "time" ? "time" : "flow";
  const select = (id: string) => {
    const next = new URLSearchParams(params);
    next.set("step", id);
    setExpanded(true);
    setParams(next, { replace: true });
  };
  const view = (value: string) => {
    const next = new URLSearchParams(params);
    next.set("view", value);
    setParams(next, { replace: true });
  };
  const back = callListReturn(params.get("return"), data.trace.user_id);
  const style = process.artifacts.find(
    (a) => a.artifact_type === "style_resolution",
  );
  const version =
    object(style?.payload).profile_version ||
    data.style_comparison?.profile_version;
  return (
    <>
      <div className="call-heading">
        <div>
          <h1>
            单次调用详情 <Badge value={stats.outcome} />
          </h1>
          <small>
            <Link to={`/users/${data.trace.user_id}`}>
              {data.trace.user_id}
            </Link>{" "}
            · {date(data.trace.created_at)} · {data.trace.id}
          </small>
        </div>
        <div className="call-actions">
          <Link className="call-button" to={back}>
            ← 返回列表
          </Link>
          <Link
            className="call-button"
            to={`/users/${data.trace.user_id}/usage/${data.trace.agent_turn_id || ""}`}
          >
            查看用量
          </Link>
        </div>
      </div>
      <div className="call-request">
        <div>
          <span className="call-muted call-small">用户输入</span>
          <div className="call-quote">
            {data.input.messages.map((m) => (
              <p key={m.item_id}>
                {m.redacted ? "[正文已脱敏]" : m.text || "[正文未记录]"}
              </p>
            ))}
            {data.input.messages.length === 0 && (
              <p>无文字输入 · {data.trace.trigger_type}</p>
            )}
          </div>
        </div>
        <span className="call-badge">
          {data.input.images.length
            ? `图片 × ${data.input.images.length}`
            : "纯文字／系统触发"}
        </span>
        {data.input.images.length > 0 && (
          <div className="call-images">
            {data.input.images.map(
              (image) =>
                image.asset_id && (
                  <a
                    key={image.item_id}
                    href={callApi.image(
                      data.trace.user_id,
                      data.trace.id,
                      image.asset_id,
                    )}
                    target="_blank"
                    rel="noreferrer"
                    aria-label="查看原始图片"
                  >
                    <img
                      src={callApi.image(
                        data.trace.user_id,
                        data.trace.id,
                        image.asset_id,
                      )}
                      alt="本轮用户图片；过期或脱敏图片不可读取"
                      loading="lazy"
                    />
                  </a>
                ),
            )}
          </div>
        )}
      </div>
      <div className="call-states">
        <span>
          业务执行 <Badge value={stats.business_status} />
        </span>
        <span>
          医生回复 <Badge value={stats.reply_status} />
        </span>
        <span>
          服务／渠道 <Badge value={stats.delivery_status} />
        </span>
        <span>
          客户端 <Badge value={stats.client_status} />
        </span>
      </div>
      <div className="call-statline">
        <span>
          服务耗时 <strong>{elapsed(data.trace.duration_ms)}</strong>
        </span>
        <span>
          主线模型请求 <strong>{stats.model_calls ?? "未知"}</strong> 次
        </span>
        <span>
          工具执行 <strong>{stats.tool_calls ?? "未知"}</strong> 次
        </span>
        <span>
          风格 <strong>{version ? String(version) : "未记录"}</strong>
        </span>
      </div>
      {(problem || data.trace.failure_code) && (
        <div className="call-alert">
          <span>
            !{" "}
            {problem?.summary ||
              data.trace.error_detail ||
              data.trace.failure_code}
            {stats.outcome === "recovered" ? " · 已修复，之前的尝试仍保留" : ""}
          </span>
          {problem && (
            <button className="call-button" onClick={() => select(problem.id)}>
              定位问题
            </button>
          )}
        </div>
      )}
      <div className="call-grid">
        <section className="call-panel">
          <div className="call-panel-head">
            <h2>处理过程</h2>
            <div className="call-mode" role="group" aria-label="过程视图">
              <button
                aria-pressed={mode === "flow"}
                onClick={() => view("flow")}
              >
                流程
              </button>
              <button
                aria-pressed={mode === "time"}
                onClick={() => view("time")}
              >
                耗时
              </button>
              {mode === "flow" && (
                <button
                  aria-pressed={expanded}
                  onClick={() => setExpanded(!expanded)}
                >
                  {expanded ? "收起内部调用" : "展开内部调用"}
                </button>
              )}
            </div>
          </div>
          {mode === "flow" ? (
            <Flow
              nodes={process.nodes}
              expanded={expanded}
              selected={selected?.id || ""}
              onSelect={select}
            />
          ) : (
            <Waterfall
              nodes={process.nodes}
              start={data.trace.created_at}
              end={data.trace.completed_at}
              selected={selected?.id || ""}
              onSelect={select}
            />
          )}
          <div className="call-end">
            <span>
              {data.trace.completed_at
                ? "主线结束 · 结果见状态栏"
                : "主线处理中 · 等待实际执行结果"}
            </span>
            <span>{elapsed(data.trace.duration_ms)}</span>
          </div>
          {(process.background_jobs.length > 0 ||
            process.background_calls.length > 0) && (
            <details className="call-background">
              <summary>
                后台任务 · {process.background_jobs.length} 个任务 /{" "}
                {process.background_calls.length} 次模型请求 · 独立计时与用量
              </summary>
              <div className="call-background-detail">
                {process.background_jobs.map((job) => (
                  <div className="call-field" key={job.id}>
                    <h3>
                      {job.kind} <Badge value={job.status} />
                    </h3>
                    <p>
                      尝试 {job.attempt_count} 次 · {date(job.created_at)}
                      {job.error_code && ` · ${job.error_code}`}
                    </p>
                  </div>
                ))}
                <Json
                  value={
                    process.background_calls.length
                      ? process.background_calls
                      : null
                  }
                />
              </div>
            </details>
          )}
        </section>
        {selected && (
          <Inspector
            key={selected.id}
            data={data}
            selected={selected}
            onSelect={select}
          />
        )}
      </div>
      <FinalOutput data={data} />
      <p className="call-footnote">
        {stats.coverage}{" "}
        原始候选稿仅在对应步骤中展示；这里不展示模型隐藏思维链。
      </p>
    </>
  );
}
