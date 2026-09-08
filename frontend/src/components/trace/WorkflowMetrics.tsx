import { useQuery } from "@tanstack/react-query";

import { api } from "../../api";
import type { TraceWorkflowReviewMetrics } from "../../types";
import { agentRoleLabel } from "./model";

export function WorkflowMetrics() {
  const query = useQuery({
    queryKey: ["workflow-review-metrics", 7],
    queryFn: () => api.workflowMetrics(7),
    staleTime: 5 * 60_000,
    retry: false,
  });
  return (
    <section className="workflow-metrics">
      <div className="section-heading">
        <div><h2>全站工作流指标</h2><p>近 7 天全部用户的已记录执行数据；列表筛选作用于下方用户链路。</p></div>
        {query.data && <span>{count(query.data.counts?.trace_count)} 条 Trace</span>}
      </div>
      {query.isPending && <p role="status">正在读取工作流指标…</p>}
      {query.isError && <div className="state-card" role="status">工作流指标暂不可用。<button type="button" onClick={() => void query.refetch()}>重试</button></div>}
      {query.data && <WorkflowMetricsData metrics={query.data} />}
    </section>
  );
}

export function WorkflowMetricsData({ metrics }: { metrics: TraceWorkflowReviewMetrics }) {
  const citations = metrics.citations;
  const nodes = Object.entries(metrics.node_failure_rates ?? {});
  const outcomes = Object.entries(metrics.outcomes_by_mode ?? {});
  const cards = [
    ["p50 延迟", duration(metrics.latency_ms?.sample_count === 0 ? null : metrics.latency_ms?.p50)],
    ["p95 延迟", duration(metrics.latency_ms?.sample_count === 0 ? null : metrics.latency_ms?.p95)],
    ["Token 总量", count(metrics.tokens?.total)],
    ["p50 Token", count(metrics.tokens?.p50)],
    ["p95 Token", count(metrics.tokens?.p95)],
    ["知识 Claim 引用覆盖率", rate(citations?.knowledge_claim_count === 0 ? null : citations?.coverage_rate)],
    ["无效引用率", rate(citations?.citation_count === 0 ? null : citations?.invalid_rate)],
  ];
  return (
    <>
      <div className="workflow-metric-grid">
        {cards.map(([label, value]) => <article className="metric" key={label}><strong>{value}</strong><span>{label}</span></article>)}
      </div>
      <p className="workflow-metric-note">延迟样本：{count(metrics.latency_ms?.sample_count)} 条；Token 工作流样本：{count(metrics.tokens?.workflow_count)} 条。未记录表示该时间窗口没有可用数据。</p>
      <p className="workflow-metric-note">知识 Claim：{count(citations?.covered_claim_count)} 条已覆盖 / {count(citations?.knowledge_claim_count)} 条；引用：{count(citations?.invalid_citation_count)} 条无效 / {count(citations?.citation_count)} 条。</p>
      <p className="workflow-metric-note">引用统计针对图内最新专业结果，不表示已采用或送达；Token 为有图工作流中已记录的原 Harness 与图节点用量。</p>
      <h3>节点失败率</h3>
      {nodes.length > 0 ? (
        <div className="workflow-metric-table"><table>
          <thead><tr><th>Agent</th><th>失败 Invocation</th><th>全部 Invocation</th><th>失败率</th></tr></thead>
          <tbody>{nodes.map(([role, node]) => <tr key={role}><th scope="row">{agentRoleLabel(role)}</th><td>{count(node.failed)}</td><td>{count(node.total)}</td><td>{rate(node.total === 0 ? null : node.rate)}</td></tr>)}</tbody>
        </table></div>
      ) : <p className="workflow-metric-note">该时间窗口没有可用的节点失败率记录。</p>}
      <h3>Legacy / Multi-Agent 执行结果对比</h3>
      <p className="workflow-metric-note">以下是回复生成状态，不代表人工质量评分或渠道送达；候选质量仍需同输入配对评审。</p>
      {outcomes.length > 0 ? (
        <div className="workflow-metric-table"><table>
          <thead><tr><th>模式</th><th>样本</th><th>生成成功</th><th>降级</th><th>失败</th></tr></thead>
          <tbody>{outcomes.map(([mode, row]) => <tr key={mode}>
            <th scope="row">{mode === "off" ? "Legacy / Off" : mode}</th>
            <td>{count(row.total)}</td><td>{count(row.succeeded)}</td>
            <td>{count(row.degraded)}</td><td>{count(row.failed)}</td>
          </tr>)}</tbody>
        </table></div>
      ) : <p className="workflow-metric-note">该时间窗口没有可用的模式对比记录。</p>}
    </>
  );
}

function valid(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

function count(value: number | null | undefined): string {
  return valid(value) ? new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 }).format(value) : "未记录";
}

function duration(value: number | null | undefined): string {
  if (!valid(value)) return "未记录";
  return value < 1000 ? `${Math.round(value)} ms` : `${(value / 1000).toFixed(2)} s`;
}

function rate(value: number | null | undefined): string {
  return valid(value) && value <= 1 ? `${(value * 100).toFixed(1)}%` : "未记录";
}
