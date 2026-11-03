import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api } from "../api";
import "./shell.css";

function NavigationItem({
  to,
  active,
  symbol,
  children,
}: {
  to: string;
  active: boolean;
  symbol: string;
  children: string;
}) {
  return (
    <Link
      to={to}
      aria-current={active ? "page" : undefined}
      className={`nav-item${active ? " active" : ""}`}
    >
      <span aria-hidden="true">{symbol}</span>
      {children}
    </Link>
  );
}

export function AdminShell({ username }: { username: string }) {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const client = useQueryClient();
  const logout = useMutation({
    mutationFn: api.logout,
    onSettled: () => {
      client.clear();
      navigate("/login", { replace: true });
    },
  });
  const userManagement =
    pathname === "/users/directory" ||
    /^\/users\/[^/]+\/(memories|records|routines)/.test(pathname);
  const userUsage = /^\/users\/[^/]+\/usage/.test(pathname);
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Link to="/users" className="brand">
          <span className="brand-mark">⌁</span>
          <strong>称小二</strong>
        </Link>
        <nav aria-label="后台主导航">
          <div className="nav-group-label">运行观察</div>
          <NavigationItem
            to="/users"
            active={
              pathname.startsWith("/users") && !userManagement && !userUsage
            }
            symbol="◎"
          >
            调用过程
          </NavigationItem>
          <NavigationItem
            to="/usage"
            active={pathname.startsWith("/usage") || userUsage}
            symbol="◷"
          >
            模型用量
          </NavigationItem>
          <div className="nav-group-label">配置与管理</div>
          <NavigationItem
            to="/users/directory"
            active={userManagement}
            symbol="♙"
          >
            用户管理
          </NavigationItem>
          <NavigationItem
            to="/styles"
            active={pathname.startsWith("/styles")}
            symbol="✎"
          >
            表达风格
          </NavigationItem>
          <NavigationItem
            to="/nutrition-knowledge"
            active={pathname.startsWith("/nutrition-knowledge")}
            symbol="⌘"
          >
            营养知识库
          </NavigationItem>
        </nav>
        <div className="sidebar-note">
          <span className="privacy-dot" />
          健康数据受保护<small>查看敏感详情会被审计</small>
        </div>
        <div className="account-panel">
          <span>
            <small>当前账号</small>
            {username}
          </span>
          <button
            type="button"
            onClick={() => logout.mutate()}
            disabled={logout.isPending}
          >
            退出
          </button>
        </div>
      </aside>
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
