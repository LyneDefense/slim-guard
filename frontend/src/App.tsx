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
  useLocation,
  useOutletContext,
  useParams,
} from "react-router-dom";

import { api, UnauthorizedError } from "./api";
import { UsagePage, UsageDetailPage } from "./features/usage/UsagePage";
import { StyleProfilePage } from "./components/style/StyleProfilePage";
import { NutritionKnowledgePage } from "./components/nutrition/NutritionKnowledgePage";
import { CallListPage } from "./features/traces/CallListPage";
import { CallDetailPage } from "./features/traces/CallDetailPage";
import type { MemoryRecord, UserDetail } from "./types";

const STATUS_LABELS: Record<string, string> = {
  accepted: "已送达",
  completed: "完成",
  succeeded: "成功",
  running: "处理中",
  started: "处理中",
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
  const location = useLocation();
  const callArea = /^\/users(?:\/directory|\/[^/]+(?:\/traces\/[^/]+)?)?\/?$/.test(location.pathname);
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
    <div className={`app-shell${callArea ? " call-shell" : ""}`}>
      <aside className="sidebar">
        <Link to="/users" className="brand">
          <span className="brand-mark">⌁</span>
          <span><strong>称小二</strong><small>Trace Console</small></span>
        </Link>
        <nav>
          {callArea && <div className="call-nav-label">运行观察</div>}
          <NavLink to="/users" className={({ isActive }) => isActive && location.pathname !== "/users/directory" ? "nav-item active" : "nav-item"}>
            <span>◎</span> 调用过程
          </NavLink>
          <NavLink to="/usage" className={({ isActive }) => isActive ? "nav-item active" : "nav-item"}><span>◷</span> 模型用量</NavLink>
          {callArea && <><div className="call-nav-label">配置与管理</div><NavLink to="/users/directory" className={({isActive}) => isActive ? "nav-item active" : "nav-item"}><span>♙</span> 用户管理</NavLink></>}
          <NavLink to="/styles" className={({ isActive }) => isActive ? "nav-item active" : "nav-item"}>
            <span>✎</span> 表达风格
          </NavLink>
          <NavLink to="/nutrition-knowledge" className={({ isActive }) => isActive ? "nav-item active" : "nav-item"}>
            <span>⌘</span> 营养知识库
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

function UserDirectoryPage() {
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
  const location = useLocation();
  const { userId = "" } = useParams();
  const query = useQuery({ queryKey: ["user", userId], queryFn: () => api.user(userId), enabled: Boolean(userId) });
  if (query.isLoading) return <div className="page"><Loading label="正在读取用户" /></div>;
  if (query.error || !query.data) return <div className="page"><Failure error={query.error} /></div>;
  const user = query.data;
  if (/^\/users\/[^/]+(?:\/traces\/[^/]+)?\/?$/.test(location.pathname)) return <Outlet context={{ user }} />;
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
        <NavLink to={`/users/${userId}/usage`}>问答用量</NavLink>
      </nav>
      <Outlet context={{ user }} />
    </div>
  );
}

function useUser(): UserDetail {
  return useOutletContext<{ user: UserDetail }>().user;
}

function MemoriesPage() {
  const user = useUser();
  const query = useQuery({ queryKey: ["memories", user.id], queryFn: () => api.memories(user.id) });
  if (query.isLoading) return <Loading />;
  if (query.error) return <Failure error={query.error} />;
  const memories = query.data ?? [];
  return <section>
    <div className="section-heading"><div><h2>用户记忆</h2><p>数据库是事实依据；Mem0 只负责帮助找到相关资料，不会覆盖这里的值。</p></div><span>{memories.length} 条</span></div>
    {memories.length === 0
      ? <div className="state-card">暂无长期记忆</div>
      : <div className="memory-grid">{memories.map((memory) => <MemoryCard key={memory.id} memory={memory} />)}</div>}
  </section>;
}

const MEMORY_LABELS: Record<string, string> = {
  "identity.preferred_name": "偏好称呼",
  "profile.height": "身高",
  "profile.exercise_habit": "长期运动习惯",
  "coaching.response_style": "回复风格",
  "food.preference": "饮食偏好",
  "exercise.preference": "运动偏好",
  "goal.target_weight": "目标体重",
  "goal.target_body_fat": "目标体脂率",
  "goal.behavior": "行为目标",
  "constraint.dietary": "饮食限制",
  "constraint.exercise": "运动限制",
  "constraint.health_context": "用户自述健康背景",
};

function MemoryCard({ memory }: { memory: MemoryRecord }) {
  const indexStatus = memory.semantic_index.status;
  const indexText = indexStatus === "completed" ? "已进入语义索引" : indexStatus === "not_queued" ? "语义索引未启用" : indexStatus === "failed" ? "语义索引同步失败" : "等待同步语义索引";
  return <article className={`memory-card memory-${memory.status}`}>
    <header><div><span className="eyebrow">{memory.kind} · {memory.memory_key}</span><h3>{MEMORY_LABELS[memory.memory_key] ?? memory.memory_key}</h3></div><StatusBadge value={memory.status} /></header>
    <p className="memory-value">{formatMemoryValue(memory)}</p>
    <dl className="memory-meta">
      <div><dt>事实来源</dt><dd>用户原话证据 · {shortId(memory.evidence_item_id)}</dd></div>
      <div><dt>生效时间</dt><dd>{formatDate(memory.valid_from)}</dd></div>
      <div><dt>可信级别</dt><dd>{memory.assertion === "user_explicit" ? "用户明确表达" : memory.assertion}</dd></div>
      <div><dt>召回索引</dt><dd>{indexText}{memory.semantic_index.attempt_count ? ` · 尝试 ${memory.semantic_index.attempt_count} 次` : ""}</dd></div>
    </dl>
    {memory.semantic_index.error_detail && <div className="event-error">{memory.semantic_index.error_code} · {memory.semantic_index.error_detail}</div>}
    <JsonView value={memory} label="查看记忆技术详情" />
  </article>;
}

function shortId(value: string | null) {
  return value ? value.slice(0, 8) : "历史记录";
}

function formatMemoryValue(memory: MemoryRecord): string {
  const value = memory.value ?? {};
  if (memory.memory_key === "profile.height" && typeof value.millimeters === "number") return `${value.millimeters / 10} cm`;
  if (memory.memory_key === "goal.target_weight" && typeof value.grams === "number") return `${value.grams / 1000} kg`;
  if (memory.memory_key === "goal.target_body_fat" && typeof value.basis_points === "number") return `${value.basis_points / 100}%`;
  if (typeof value.statement === "string") return value.statement;
  if (typeof value.name === "string") return value.name;
  if (typeof value.item === "string") return `${value.item} · ${String(value.stance ?? "")}`;
  if (typeof value.activity === "string") return `${value.activity} · ${String(value.stance ?? "")}`;
  return JSON.stringify(value, null, 2);
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
        <Route path="users" element={<CallListPage />} />
        <Route path="users/directory" element={<UserDirectoryPage />} />
        <Route path="usage" element={<UsagePage />} />
        <Route path="usage/jobs" element={<UsagePage jobs />} />
        <Route path="usage/jobs/:jobId" element={<UsageDetailPage />} />
        <Route path="styles" element={<StyleProfilePage />} />
        <Route path="styles/:styleId/:tab" element={<StyleProfilePage />} />
        <Route path="nutrition-knowledge" element={<NutritionKnowledgePage />} />
        <Route path="users/:userId" element={<UserLayout />}>
          <Route index element={<CallListPage />} />
          <Route path="traces/:traceId" element={<CallDetailPage />} />
          <Route path="memories" element={<MemoriesPage />} />
          <Route path="records" element={<RecordsPage />} />
          <Route path="routines" element={<RoutinesPage />} />
          <Route path="usage" element={<UsagePage />} />
          <Route path="usage/:requestId" element={<UsageDetailPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/users" replace />} />
      </Route>
    </Routes>
  );
}
