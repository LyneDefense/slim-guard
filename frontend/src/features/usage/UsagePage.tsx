import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { usageApi, type Totals } from "./api";
import "./usage.css";

const roles: Record<string, string> = {
  core: "Core Agent", conversation: "聊天 Agent", nutrition_expert: "营养 Agent",
  response_style: "风格 Agent", response_reviewer: "回复审查 Agent",
};
const role = (name: string | null) => name ? roles[name] || name : "非 Agent 组件";
const components: Record<string, string> = { core: "主流程生成", agent_generation: "Agent 生成", style_internal_review: "风格内部审查", image_inspection: "图片理解", memory_recall: "记忆召回", memory_extraction: "记忆提取", nutrition_retrieval: "营养检索", rag_answerability: "证据支持性判断", embedding: "向量化", rerank: "重排" };
const componentName = (name: string) => components[name] || name;
const statuses: Record<string, string> = { succeeded: "返回成功", failed: "请求失败", running: "进行中", cancelled: "已取消" };
const number = (value: number | null | undefined) => value == null ? "未知" : value.toLocaleString();
const date = (value?: string) => value ? new Date(value).toLocaleString("zh-CN") : "—";

function Pager({ offset, total, onChange }: { offset: number; total: number; onChange: (n: number) => void }) {
  return <div className="usage-pager"><button disabled={!offset} onClick={() => onChange(Math.max(0, offset - 20))}>上一页</button>
    <span>{total ? `${offset + 1}–${Math.min(offset + 20, total)}` : "0"} / {total}</span>
    <button disabled={offset + 20 >= total} onClick={() => onChange(offset + 20)}>下一页</button></div>;
}

function Counters({ data }: { data: Totals }) {
  return <div className="usage-counters">{[
    ["模型请求", data.calls], ["含模型的 Agent 调用", data.invocations], ["已知输入 Token", data.input_tokens],
    ["已知输出 Token", data.output_tokens], ["已知总 Token", data.total_tokens],
    ["用量未知的请求", data.unknown_calls || 0], ["失败请求", data.failed_calls || 0], ["尚未结束的请求", data.running_calls || 0],
  ].map(([label, value]) => <div key={String(label)}><strong>{number(Number(value))}</strong><span>{label}</span></div>)}</div>;
}

export function UsagePage({ jobs = false }: { jobs?: boolean }) {
  const { userId } = useParams();
  // Remount by owner to avoid carrying pagination between different users.
  return <UsageList key={`${userId || "all"}-${jobs}`} userId={userId} jobs={jobs} />;
}

function UsageList({ userId, jobs }: { userId?: string; jobs: boolean }) {
  const [offset, setOffset] = useState(0);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const params = new URLSearchParams({ offset: String(offset), limit: "20" });
  if (start) params.set("start", new Date(start).toISOString());
  if (end) params.set("end", new Date(end).toISOString());
  const path = userId ? `users/${encodeURIComponent(userId)}/requests` : jobs ? "jobs" : "users";
  const valid = !start || !end || new Date(start) < new Date(end);
  const query = useQuery({ queryKey: ["usage", path, params.toString()], queryFn: () => usageApi.list(path, params), enabled: valid, refetchInterval: 10000 });
  return <section className={`usage ${userId ? "" : "page"}`}>
    <h1>{userId ? "问答用量" : jobs ? "系统任务用量" : "用户用量统计"}</h1>
    <p>一条用户输入及其完整回复计为一次问答；多角色气泡和卡片不拆开计数。后续确认是新问答。异步用量回填原问答。</p>
    {!userId && <nav className="tabs"><Link to="/usage">按用户</Link><Link to="/usage/jobs">系统任务（无用户归属）</Link></nav>}
    <div className="usage-filters"><label>开始时间<input type="datetime-local" value={start} onChange={e => { setStart(e.target.value); setOffset(0); }} /></label>
      <label>结束时间（不含）<input type="datetime-local" value={end} onChange={e => { setEnd(e.target.value); setOffset(0); }} /></label></div>
    {!valid && <p role="alert">结束时间必须晚于开始时间。</p>}
    {query.isLoading && <p>正在读取用量…</p>}
    {query.error && <p role="alert">读取失败：{String(query.error)}</p>}
    {query.data && valid && <><div className="usage-table"><table><thead><tr><th>{userId || jobs ? "问答 / 任务" : "用户"}</th><th>模型请求</th><th>含模型的 Agent 调用</th><th>已知输入</th><th>已知输出</th><th>已知总 Token</th><th>未知用量</th></tr></thead><tbody>
      {query.data.items.map(row => <tr key={row.id || row.user_id}><td><Link to={userId ? `/users/${userId}/usage/${row.id}` : jobs ? `/usage/jobs/${row.id}` : `/users/${row.user_id}/usage`}>
        {row.started_at ? date(row.started_at) : row.user_id}</Link><small>{row.id} {row.category} {row.status}{row.exchanges != null ? ` · ${row.exchanges} 次问答/任务` : ""}</small></td>
        <td>{number(row.calls)}</td><td>{number(row.invocations)}</td><td>{number(row.input_tokens)}</td><td>{number(row.output_tokens)}</td><td>{row.calls ? number(row.total_tokens) : "未记录调用"}</td><td>{number(row.unknown_calls)}</td></tr>)}
    </tbody></table></div>{!query.data.items.length && <p>所选范围暂无用量记录。</p>}
      <Pager offset={offset} total={query.data.total} onChange={setOffset} /></>}
    <p className="usage-note">只汇总实际调用，不累计父子 Agent 的重复总数。历史未采集用量不补猜为零；外部 Mem0 内部模型消耗尚不可见。用户汇总按调用发生时间筛选，问答列表按问答开始时间筛选。</p>
  </section>;
}

export function UsageDetailPage() {
  const { userId, requestId, jobId } = useParams();
  const [offset, setOffset] = useState(0);
  const path = userId ? `users/${encodeURIComponent(userId)}/requests/${encodeURIComponent(requestId || "")}` : `jobs/${encodeURIComponent(jobId || "")}`;
  const query = useQuery({ queryKey: ["usage-detail", path, offset], queryFn: () => usageApi.detail(path, offset), refetchInterval: 10000 });
  const data = query.data;
  return <section className={`usage ${userId ? "" : "page"}`}>
    <Link to={userId ? `/users/${userId}/usage` : "/usage/jobs"}>← 返回用量列表</Link><h2>本次{userId ? "问答" : "任务"}用量</h2><p>{requestId || jobId}</p>
    {query.isLoading && <p>正在读取…</p>}{query.error && <p role="alert">读取失败：{String(query.error)}</p>}
    {data && <>{data.trace_id && userId && <Link to={`/users/${userId}/traces/${data.trace_id}`}>查看这次问答的用户输入、实际回复和流程</Link>}<Counters data={data.summary} /><p className="usage-note">{data.coverage}</p>
      <h3>按 Agent 汇总（自身消耗）</h3><p>共 {data.executions} 次 Agent 执行，包括没有发起模型请求的执行。历史未采集的请求不补算。</p><div className="usage-table"><table><thead><tr><th>归属</th><th>Agent 执行</th><th>模型请求</th><th>已知总 Token</th><th>未知用量</th></tr></thead><tbody>
        {data.agents.map(a => <tr key={a.agent_role || "service"}><td>{role(a.agent_role)}</td><td>{a.agent_role ? number(a.executions ?? a.invocations) : "—"}</td><td>{number(a.calls)}</td><td>{number(a.total_tokens)}</td><td>{number(a.unknown_calls)}</td></tr>)}
      </tbody></table></div>
      <details><summary>组件分项</summary>{data.groups.map(g => <p key={`${g.agent_role}-${g.component}-${g.execution_kind}`}>{role(g.agent_role)} · {g.component} · {g.execution_kind}：{g.calls} 次请求 / {number(g.total_tokens)} 已知 Token / {g.unknown_calls} 次未知</p>)}</details>
      <h3>逐次模型请求</h3>{data.items.map(call => <details className="usage-call" key={call.id}>
        <summary>{role(call.agent_role)} · {componentName(call.component)} · {call.model} · {number(call.total_tokens)} Token · {statuses[call.status] || call.status}</summary>
        <dl>{Object.entries({ "时间": date(call.started_at), "用途": call.purpose, "输入 Token": number(call.input_tokens), "输出 Token": number(call.output_tokens), "耗时 ms": number(call.duration_ms), "调用 ID": call.id, "Agent Invocation": call.invocation_id, "父 Invocation": call.parent_invocation_id, "工具调用": call.tool_call_id, "错误类型": call.error_type }).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value || "—"}</dd></div>)}</dl>
      </details>)}<Pager offset={offset} total={data.total} onChange={setOffset} /></>}
  </section>;
}
