import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Link,
  Navigate,
  NavLink,
  Outlet,
  Route,
  Routes,
  useNavigate,
  useOutletContext,
  useParams,
} from "react-router-dom";

import { api, UnauthorizedError } from "./api";
import type { TimelineEvent, TraceDetail, TraceSummary, UserDetail } from "./types";

const STATUS_LABELS: Record<string, string> = {
  accepted: "已送达",
  completed: "完成",
  succeeded: "成功",
  running: "处理中",
  sending: "发送中",
  planned: "已计划",
  pending: "等待中",
  pending_review: "待审核",
  waiting: "等待确认",
  degraded: "已降级",
  failed: "失败",
  unknown: "结果未知",
  skipped: "已跳过",
  deferred_external_session: "人工会话中",
};

const STAGE_LABELS: Record<string, string> = {
  input: "输入",
  context: "上下文",
  decision: "模型判断",
  action: "动作",
  observation: "观察",
  output: "输出",
  delivery: "投递",
  system: "系统",
};

const TRIGGER_LABELS: Record<string, string> = {
  user_message: "用户发来消息",
  user_confirmation: "用户确认操作",
  daily_reminder: "每日提醒",
  weight_reminder: "体重提醒",
  meal_reminder: "饮食提醒",
  daily_review: "每日复盘",
  weekly_review: "每周复盘",
};

function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function formatDuration(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value < 1000) return `${value} ms`;
  return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)} s`;
}

function StatusBadge({ value }: { value: string }) {
  const tone = ["accepted", "completed", "succeeded"].includes(value)
    ? "good"
    : ["failed", "unknown", "degraded"].includes(value)
      ? "bad"
      : ["waiting", "pending_review", "sending", "running"].includes(value)
        ? "warn"
        : "neutral";
  return <span className={`status status-${tone}`}>{STATUS_LABELS[value] ?? value}</span>;
}

function Loading({ label = "正在读取数据" }: { label?: string }) {
  return <div className="state-card"><span className="spinner" />{label}</div>;
}

function Failure({ error }: { error: unknown }) {
  return <div className="state-card state-error">{error instanceof Error ? error.message : "读取失败"}</div>;
}

function JsonView({ value, label = "查看技术详情" }: { value: unknown; label?: string }) {
  return (
    <details className="json-view">
      <summary>{label}</summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}

function ProtectedShell() {
  const query = useQuery({
    queryKey: ["admin-session"],
    queryFn: api.session,
    retry: false,
    staleTime: 60_000,
  });
  if (query.isLoading) return <div className="login-shell"><Loading label="正在验证登录状态" /></div>;
  if (query.error instanceof UnauthorizedError) return <Navigate to="/login" replace />;
  if (query.error || !query.data) return <div className="login-shell"><Failure error={query.error} /></div>;
  return <Shell username={query.data.username} />;
}

function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const session = useQuery({
    queryKey: ["admin-session"],
    queryFn: api.session,
    retry: false,
    staleTime: 60_000,
  });
  const login = useMutation({
    mutationFn: () => api.login(username.trim(), password),
    onSuccess: (data) => {
      queryClient.setQueryData(["admin-session"], data);
      navigate("/users", { replace: true });
    },
  });

  if (session.data) return <Navigate to="/users" replace />;
  return (
    <div className="login-shell">
      <section className="login-card">
        <div className="login-brand"><span className="brand-mark">S</span><div><strong>SlimGuard</strong><small>Trace Console</small></div></div>
        <p className="eyebrow">ADMIN ACCESS</p>
        <h1>登录管理后台</h1>
        <p className="login-intro">查看按用户隔离的对话、记忆与完整输出链路。</p>
        <form onSubmit={(event) => { event.preventDefault(); login.mutate(); }}>
          <label>用户名<input autoComplete="username" autoFocus value={username} onChange={(event) => setUsername(event.target.value)} required /></label>
          <label>密码<input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required /></label>
          {login.error && <div className="login-error">{login.error instanceof UnauthorizedError ? "用户名或密码不正确" : login.error.message}</div>}
          <button type="submit" disabled={login.isPending || !username.trim() || !password}>{login.isPending ? "正在登录…" : "登录"}</button>
        </form>
        <small className="login-security">登录凭据由 SlimGuard 后端统一验证，不经过前端或 Nginx 保存。</small>
      </section>
    </div>
  );
}

function Shell({ username }: { username: string }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const logout = useMutation({
    mutationFn: api.logout,
    onSettled: () => {
      queryClient.clear();
      navigate("/login", { replace: true });
    },
  });
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Link to="/users" className="brand">
          <span className="brand-mark">S</span>
          <span><strong>SlimGuard</strong><small>Trace Console</small></span>
        </Link>
        <nav>
          <NavLink to="/users" className={({ isActive }) => isActive ? "nav-item active" : "nav-item"}>
            <span>◎</span> 用户中心
          </NavLink>
        </nav>
        <div className="sidebar-note">
          <span className="privacy-dot" />
          健康数据受保护
          <small>查看敏感详情会被审计</small>
        </div>
        <div className="account-panel"><span><small>当前账号</small>{username}</span><button type="button" onClick={() => logout.mutate()} disabled={logout.isPending}>退出</button></div>
      </aside>
      <main className="main"><Outlet /></main>
    </div>
  );
}

function UsersPage() {
  const [draft, setDraft] = useState("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const query = useQuery({ queryKey: ["users", search, offset], queryFn: () => api.users(search, offset), refetchInterval: 15_000 });

  return (
    <div className="page">
      <header className="page-header">
        <div><p className="eyebrow">USER OBSERVABILITY</p><h1>用户中心</h1><p>从用户进入，查看其独立的对话、记忆与输出链路。</p></div>
        {query.data && <div className="count-chip"><strong>{query.data.total}</strong><span>用户</span></div>}
      </header>
      <form className="search-bar" onSubmit={(event) => { event.preventDefault(); setOffset(0); setSearch(draft.trim()); }}>
        <span>⌕</span>
        <input value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="搜索昵称或内部用户 UUID" />
        <button type="submit">搜索</button>
      </form>
      {query.isLoading && <Loading />}
      {query.error && <Failure error={query.error} />}
      {query.data && (
        <>
          <div className="user-grid">
            {query.data.items.map((user) => (
              <Link className="user-card" to={`/users/${user.id}`} key={user.id}>
                <div className="avatar">{(user.nickname || "匿").slice(0, 1)}</div>
                <div className="user-main">
                  <div className="user-title"><h2>{user.nickname || "未命名用户"}</h2>{user.issue_count > 0 && <span className="issue">{user.issue_count} 异常</span>}</div>
                  <code>{user.user_ref}</code>
                  <div className="user-meta"><span>最近活跃 {formatDate(user.last_seen_at)}</span><span>{user.trace_count} 条链路</span>{user.last_delivery_status && <StatusBadge value={user.last_delivery_status} />}</div>
                </div>
                <span className="arrow">→</span>
              </Link>
            ))}
          </div>
          {query.data.items.length === 0 && <div className="state-card">没有找到用户</div>}
          <div className="pager">
            <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 30))}>上一页</button>
            <span>{offset + 1}–{Math.min(offset + 30, query.data.total)} / {query.data.total}</span>
            <button disabled={offset + 30 >= query.data.total} onClick={() => setOffset(offset + 30)}>下一页</button>
          </div>
        </>
      )}
    </div>
  );
}

function UserLayout() {
  const { userId = "" } = useParams();
  const query = useQuery({ queryKey: ["user", userId], queryFn: () => api.user(userId), enabled: Boolean(userId) });
  if (query.isLoading) return <div className="page"><Loading label="正在读取用户" /></div>;
  if (query.error || !query.data) return <div className="page"><Failure error={query.error} /></div>;
  const user = query.data;
  return (
    <div className="page">
      <Link to="/users" className="back-link">← 返回用户列表</Link>
      <header className="user-hero">
        <div className="avatar avatar-large">{(user.nickname || "匿").slice(0, 1)}</div>
        <div><p className="eyebrow">USER · {user.user_ref}</p><h1>{user.nickname || "未命名用户"}</h1><p>最近活跃 {formatDate(user.last_seen_at)} · 首次出现 {formatDate(user.first_seen_at)}</p></div>
      </header>
      <nav className="tabs">
        <NavLink end to={`/users/${userId}`}>输出链路</NavLink>
        <NavLink to={`/users/${userId}/memories`}>记忆</NavLink>
        <NavLink to={`/users/${userId}/records`}>健康记录</NavLink>
        <NavLink to={`/users/${userId}/routines`}>提醒日程</NavLink>
      </nav>
      <Outlet context={{ user }} />
    </div>
  );
}

function useUser(): UserDetail {
  return useOutletContext<{ user: UserDetail }>().user;
}

function TraceList() {
  const user = useUser();
  const [offset, setOffset] = useState(0);
  const [generation, setGeneration] = useState("");
  const [delivery, setDelivery] = useState("");
  const query = useQuery({ queryKey: ["traces", user.id, offset, generation, delivery], queryFn: () => api.traces(user.id, offset, generation, delivery), refetchInterval: 5_000 });
  return (
    <section>
      <div className="metric-grid">
        <Metric label="输出链路" value={user.counts.trace_count ?? 0} />
        <Metric label="体重记录" value={user.counts.weight_count ?? 0} />
        <Metric label="体脂记录" value={user.counts.body_fat_count ?? 0} />
        <Metric label="饮食记录" value={user.counts.meal_count ?? 0} />
        <Metric label="当前记忆" value={user.counts.memory_count ?? 0} />
      </div>
      {(user.active_handoff || user.routine) && (
        <div className="context-strip">
          {user.active_handoff && (
            <article>
              <span className="eyebrow">ACTIVE HANDOFF</span>
              <strong>{user.active_handoff.objective}</strong>
              <small>有效至 {formatDate(user.active_handoff.expires_at)}</small>
            </article>
          )}
          {user.routine && (
            <article>
              <span className="eyebrow">ROUTINE</span>
              <strong>{user.routine.timezone || "已配置提醒"}</strong>
              <small>进入“提醒日程”查看详细计划</small>
            </article>
          )}
        </div>
      )}
      <div className="section-heading"><div><h2>输出链路</h2><p>每条回复的生成状态和投递状态分开呈现。</p></div></div>
      <div className="filters">
        <label>生成状态<select value={generation} onChange={(event) => { setGeneration(event.target.value); setOffset(0); }}><option value="">全部</option><option value="succeeded">成功</option><option value="waiting">等待确认</option><option value="degraded">降级</option><option value="failed">失败</option><option value="unknown">未知</option><option value="skipped">跳过</option></select></label>
        <label>投递状态<select value={delivery} onChange={(event) => { setDelivery(event.target.value); setOffset(0); }}><option value="">全部</option><option value="accepted">已送达</option><option value="sending">发送中</option><option value="pending_review">待审核</option><option value="failed">失败</option><option value="unknown">未知</option><option value="deferred_external_session">人工会话中</option><option value="skipped">跳过</option></select></label>
      </div>
      {query.isLoading && <Loading />}
      {query.error && <Failure error={query.error} />}
      {query.data && <div className="trace-list">{query.data.items.map((trace) => <TraceRow key={trace.id} trace={trace} userId={user.id} />)}</div>}
      {query.data && query.data.items.length === 0 && <div className="state-card">这个用户还没有可查看的 Trace</div>}
      {query.data && <div className="pager"><button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 30))}>上一页</button><span>{offset + 1}–{Math.min(offset + 30, query.data.total)} / {query.data.total}</span><button disabled={offset + 30 >= query.data.total} onClick={() => setOffset(offset + 30)}>下一页</button></div>}
    </section>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return <div className="metric"><strong>{value}</strong><span>{label}</span></div>;
}

function TraceRow({ trace, userId }: { trace: TraceSummary; userId: string }) {
  return (
    <Link className="trace-row" to={`/users/${userId}/traces/${trace.id}`}>
      <div className={`trace-icon ${trace.generation_status === "succeeded" ? "ok" : "attention"}`}>{trace.generation_status === "succeeded" ? "✓" : "!"}</div>
      <div className="trace-primary"><div><strong>{TRIGGER_LABELS[trace.trigger_type] ?? trace.trigger_type}</strong><code>{trace.id.slice(0, 8)}</code></div><span>{formatDate(trace.created_at)} · {formatDuration(trace.duration_ms)}</span></div>
      <div className="trace-statuses"><label>生成 <StatusBadge value={trace.generation_status} /></label><label>投递 <StatusBadge value={trace.delivery_status} /></label></div>
      <span className="arrow">→</span>
    </Link>
  );
}

function TracePage() {
  const user = useUser();
  const { traceId = "" } = useParams();
  const query = useQuery({ queryKey: ["trace", user.id, traceId], queryFn: () => api.trace(user.id, traceId), refetchInterval: 5_000 });
  if (query.isLoading) return <Loading label="正在重建输出链路" />;
  if (query.error || !query.data) return <Failure error={query.error} />;
  const data = query.data;
  return (
    <section>
      <Link to={`/users/${user.id}`} className="back-link">← 返回该用户的链路</Link>
      <div className="trace-heading">
        <div><p className="eyebrow">TRACE · {data.trace.id}</p><h2>{TRIGGER_LABELS[data.trace.trigger_type] ?? data.trace.trigger_type}</h2><p>{formatDate(data.trace.created_at)} · 总耗时 {formatDuration(data.trace.duration_ms)}</p></div>
        <div className="trace-statuses"><label>生成 <StatusBadge value={data.trace.generation_status} /></label><label>投递 <StatusBadge value={data.trace.delivery_status} /></label></div>
      </div>
      {data.trace.failure_code && <div className="alert"><strong>{data.trace.failure_code}</strong><span>{data.trace.error_detail}</span></div>}
      {data.output && <article className="output-card"><div><span className="eyebrow">FINAL OUTPUT · {data.output.kind}</span><StatusBadge value={data.output.status} /></div><p>{data.output.content}</p><small>平台消息 ID · {data.output.platform_msgid}</small></article>}
      <ExecutionOverview data={data} />
      <ContextSources data={data} />
      <div className="section-heading"><div><h2>Agent 是怎么完成这次回复的</h2><p>按 Harness 的真实事件解释上下文、模型动作、工具观察和投递过程。</p></div><span>{data.timeline.length} 个步骤</span></div>
      <div className="trace-boundary"><strong>关于“思考”</strong><span>这里展示模型明确输出的工具选择和可验证观察，不展示、补写或猜测模型隐藏的逐字思维。</span></div>
      <div className="timeline">{data.timeline.map((event, index) => <TimelineItem key={`${event.event_type}-${event.id}`} event={event} index={index + 1} />)}</div>
      {data.tool_executions.length > 0 && <section className="detail-block"><h3>工具执行原始账本</h3><p>供工程排障和核对幂等键使用，日常查看以上面的白话步骤为准。</p><JsonView value={data.tool_executions} label="展开原始工具数据" /></section>}
      {data.turn && <section className="detail-block"><h3>Harness Turn 技术信息</h3><JsonView value={data.turn} label="展开 Turn 原始数据" /></section>}
      <p className="privacy-footnote">敏感健康数据 · 已脱敏事件 {data.privacy.redacted_item_count} 条 · 本次查看已写入审计记录</p>
    </section>
  );
}

function ContextSources({ data }: { data: TraceDetail }) {
  if (data.context_sources.length === 0) return null;
  return (
    <section className="context-sources">
      <div className="section-heading">
        <div><h2>本轮实际使用的记忆与数据来源</h2><p>按保存方式分开显示，避免把最近聊天误认为长期记忆或健康记录。</p></div>
      </div>
      <div className="source-grid">
        {data.context_sources.map((source) => (
          <article className={`source-card source-${source.kind}`} key={source.kind}>
            <header><div><h3>{source.title}</h3><p>{source.description}</p></div><span>{source.retention}</span></header>
            {source.items.length === 0
              ? <div className="source-empty">本轮没有带入这一类数据</div>
              : <dl>{source.items.map((item, index) => <div key={`${item.label}-${index}`}><dt>{item.label}</dt><dd>{item.value}</dd><small>{item.detail}</small></div>)}</dl>}
          </article>
        ))}
      </div>
    </section>
  );
}

function ExecutionOverview({ data }: { data: TraceDetail }) {
  const summary = data.execution_summary;
  const isHarness = summary.architecture === "harness";
  return (
    <section className="execution-overview">
      <div className="overview-copy">
        <span className="architecture-badge">{isHarness ? "HARNESS ARCHITECTURE" : "SERVICE TRACE"}</span>
        <h3>{isHarness ? "本轮由 Agent Harness 编排" : "这是一条服务级输出链路"}</h3>
        <p>{isHarness ? "Harness 先冻结上下文，再让模型选择回复或工具；每次工具结果都会作为新观察返回给模型。" : "这条链路没有关联到可重建的 Harness Turn，通常来自历史数据或非 Agent 流程。"}</p>
      </div>
      <div className="overview-metrics">
        <Metric label="上下文快照" value={summary.context_snapshot_count} />
        <Metric label="模型调用" value={summary.model_call_count} />
        <Metric label="工具动作" value={summary.tool_call_count} />
        <Metric label="工具观察" value={summary.observation_count} />
      </div>
      {data.agent && <dl className="agent-manifest">
        <div><dt>文本模型</dt><dd>{data.agent.text_model ?? "—"}</dd></div>
        <div><dt>上下文策略</dt><dd>{data.agent.context_policy_version ?? "—"}</dd></div>
        <div><dt>记忆策略</dt><dd>{data.agent.memory_policy_version ?? "—"}</dd></div>
        <div><dt>可用工具</dt><dd>{data.agent.tool_count} 个</dd></div>
        <div><dt>代码版本</dt><dd>{data.agent.code_revision}</dd></div>
      </dl>}
    </section>
  );
}

function TimelineItem({ event, index }: { event: TimelineEvent; index: number }) {
  const failed = ["failed", "unknown"].includes(event.status);
  const view = event.presentation;
  return (
    <article className={`timeline-item stage-${view.stage} ${failed ? "timeline-failed" : ""}`}>
      <div className="timeline-rail"><span>{failed ? "!" : index}</span></div>
      <div className="timeline-card">
        <header><div><span className="stage-label">{STAGE_LABELS[view.stage] ?? view.stage}</span><span className="component">{event.component}</span><h3>{view.title}</h3></div><div className="timeline-meta"><StatusBadge value={event.status} /><span>{formatDuration(event.duration_ms)}</span><time>{formatDate(event.started_at)}</time></div></header>
        <p className="event-summary">{view.summary}</p>
        {view.facts.length > 0 && <dl className="event-facts">{view.facts.map((fact, factIndex) => <div key={`${fact.label}-${factIndex}`}><dt>{fact.label}</dt><dd>{fact.value}</dd></div>)}</dl>}
        {event.error_code && <div className="event-error">{event.error_code} {event.error_detail}</div>}
        {event.redacted && <div className="redacted">正文已按 {event.redaction_policy} 脱敏</div>}
        <JsonView value={{ operation: event.operation, details: event.details }} />
      </div>
    </article>
  );
}

function MemoriesPage() {
  const user = useUser();
  const query = useQuery({ queryKey: ["memories", user.id], queryFn: () => api.memories(user.id) });
  if (query.isLoading) return <Loading />;
  if (query.error) return <Failure error={query.error} />;
  return <DataCards title="用户记忆" subtitle="长期记忆事实及其来源、状态和敏感级别。" items={query.data ?? []} empty="暂无长期记忆" />;
}

function RecordsPage() {
  const user = useUser();
  const query = useQuery({ queryKey: ["records", user.id], queryFn: () => api.records(user.id) });
  if (query.isLoading) return <Loading />;
  if (query.error) return <Failure error={query.error} />;
  return <>{Object.entries(query.data ?? {}).map(([name, items]) => <DataCards key={name} title={{ weights: "体重", body_fat: "体脂", meals: "饮食", exercises: "运动" }[name] ?? name} items={items} empty="暂无记录" />)}</>;
}

function RoutinesPage() {
  const user = useUser();
  const query = useQuery({ queryKey: ["routines", user.id], queryFn: () => api.routines(user.id) });
  if (query.isLoading) return <Loading />;
  if (query.error) return <Failure error={query.error} />;
  return <><DataCards title="提醒设置" items={query.data?.preference ? [query.data.preference] : []} empty="未配置提醒" /><DataCards title="调度记录" items={query.data?.jobs ?? []} empty="暂无调度记录" /></>;
}

function DataCards({ title, subtitle, items, empty }: { title: string; subtitle?: string; items: Array<Record<string, unknown>>; empty: string }) {
  return <section><div className="section-heading"><div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div><span>{items.length} 条</span></div>{items.length === 0 ? <div className="state-card">{empty}</div> : <div className="data-grid">{items.map((item, index) => <article className="data-card" key={String(item.id ?? index)}><JsonView value={item} /></article>)}</div>}</section>;
}

export function App() {
  return (
    <Routes>
      <Route path="login" element={<LoginPage />} />
      <Route element={<ProtectedShell />}>
        <Route index element={<Navigate to="/users" replace />} />
        <Route path="users" element={<UsersPage />} />
        <Route path="users/:userId" element={<UserLayout />}>
          <Route index element={<TraceList />} />
          <Route path="traces/:traceId" element={<TracePage />} />
          <Route path="memories" element={<MemoriesPage />} />
          <Route path="records" element={<RecordsPage />} />
          <Route path="routines" element={<RoutinesPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/users" replace />} />
      </Route>
    </Routes>
  );
}
