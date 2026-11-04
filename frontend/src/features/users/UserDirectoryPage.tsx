import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { api } from "../../api";
import { userCallsPath } from "./navigation";
import { formatDate, StatusBadge, Loading, Failure } from "./components";
import "./users.css";

export function UserDirectoryPage({
  mode = "manage",
}: {
  mode?: "calls" | "manage";
}) {
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const search = params.get("q") || "";
  const offset = Math.max(0, Number(params.get("offset")) || 0);
  const [draft, setDraft] = useState(search);
  useEffect(() => setDraft(search), [search]);
  const paginate = (value: number) => {
    const next = new URLSearchParams(params);
    next.set("offset", String(Math.max(0, value)));
    setParams(next);
  };
  const query = useQuery({
    queryKey: ["users", search, offset],
    queryFn: () => api.users(search, offset),
    refetchInterval: 15_000,
  });

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <p className="eyebrow">USER OBSERVABILITY</p>
          <h1>{mode === "calls" ? "选择用户" : "用户中心"}</h1>
          <p>
            {mode === "calls"
              ? "先选择用户，再查看该用户的调用记录与处理详情。"
              : "从用户进入，查看其独立的对话、记忆与输出链路。"}
          </p>
        </div>
        {query.data && (
          <div className="count-chip">
            <strong>{query.data.total}</strong>
            <span>用户</span>
          </div>
        )}
      </header>
      <form
        className="search-bar"
        onSubmit={(event) => {
          event.preventDefault();
          const next = new URLSearchParams();
          if (draft.trim()) next.set("q", draft.trim());
          setParams(next);
        }}
      >
        <span>⌕</span>
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          aria-label="搜索用户"
          placeholder="搜索昵称或内部用户 UUID"
        />
        <button type="submit">搜索</button>
      </form>
      {query.isLoading && <Loading />}
      {query.error && <Failure error={query.error} />}
      {query.data && (
        <>
          <div className="user-grid">
            {query.data.items.map((user) => (
              <Link
                className="user-card"
                to={
                  mode === "calls"
                    ? `${userCallsPath(user.id)}?users_return=${encodeURIComponent(location.pathname + location.search)}`
                    : userCallsPath(user.id)
                }
                key={user.id}
              >
                <div className="avatar">
                  {(user.nickname || "匿").slice(0, 1)}
                </div>
                <div className="user-main">
                  <div className="user-title">
                    <h2>{user.nickname || "未命名用户"}</h2>
                    {user.issue_count > 0 && (
                      <span className="issue">{user.issue_count} 异常</span>
                    )}
                  </div>
                  <code>{user.user_ref}</code>
                  <div className="user-meta">
                    <span>最近活跃 {formatDate(user.last_seen_at)}</span>
                    <span>{user.trace_count} 条链路</span>
                    {user.last_delivery_status && (
                      <StatusBadge value={user.last_delivery_status} />
                    )}
                  </div>
                </div>
                <span className="arrow">
                  {mode === "calls" ? "查看调用 →" : "→"}
                </span>
              </Link>
            ))}
          </div>
          {query.data.items.length === 0 && (
            <div className="state-card">没有找到用户</div>
          )}
          <div className="pager">
            <button
              disabled={offset === 0}
              onClick={() => paginate(offset - 30)}
            >
              上一页
            </button>
            <span>
              {query.data.total ? offset + 1 : 0}–
              {Math.min(offset + 30, query.data.total)} / {query.data.total}
            </span>
            <button
              disabled={offset + 30 >= query.data.total}
              onClick={() => paginate(offset + 30)}
            >
              下一页
            </button>
          </div>
        </>
      )}
    </div>
  );
}
