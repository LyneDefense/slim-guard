import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Navigate, useNavigate } from "react-router-dom";
import { api, UnauthorizedError } from "../api";
import { AdminShell } from "./AdminShell";
import "./auth.css";

function Loading({ label }: { label: string }) {
  return (
    <div className="state-card">
      <span className="spinner" />
      {label}
    </div>
  );
}
function Failure({ error }: { error: unknown }) {
  return (
    <div className="state-card state-error">
      {error instanceof Error ? error.message : "读取失败"}
    </div>
  );
}

export function ProtectedShell() {
  const query = useQuery({
    queryKey: ["admin-session"],
    queryFn: api.session,
    retry: false,
    staleTime: 60_000,
  });
  if (query.isLoading)
    return (
      <div className="login-shell">
        <Loading label="正在验证登录状态" />
      </div>
    );
  if (query.error instanceof UnauthorizedError)
    return <Navigate to="/login" replace />;
  if (query.error || !query.data)
    return (
      <div className="login-shell">
        <Failure error={query.error} />
      </div>
    );
  return <AdminShell username={query.data.username} />;
}

export function LoginPage() {
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
        <div className="login-brand">
          <span className="brand-mark">⌁</span>
          <div>
            <strong>称小二</strong>
            <small>管理工作台</small>
          </div>
        </div>
        <p className="eyebrow">ADMIN ACCESS</p>
        <h1>登录管理后台</h1>
        <p className="login-intro">
          查看按用户隔离的对话、记忆与完整输出链路。
        </p>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            login.mutate();
          }}
        >
          <label>
            用户名
            <input
              autoComplete="username"
              autoFocus
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              required
            />
          </label>
          <label>
            密码
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </label>
          {login.error && (
            <div className="login-error">
              {login.error instanceof UnauthorizedError
                ? "用户名或密码不正确"
                : login.error.message}
            </div>
          )}
          <button
            type="submit"
            disabled={login.isPending || !username.trim() || !password}
          >
            {login.isPending ? "正在登录…" : "登录"}
          </button>
        </form>
        <small className="login-security">
          登录凭据由 SlimGuard 后端统一验证，不经过前端或 Nginx 保存。
        </small>
      </section>
    </div>
  );
}
