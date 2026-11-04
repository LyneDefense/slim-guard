import { useQuery } from "@tanstack/react-query";
import {
  Link,
  NavLink,
  Outlet,
  useLocation,
  useParams,
} from "react-router-dom";
import { api } from "../../api";
import type { MemoryRecord } from "../../types";
import { useUser } from "./context";
import { formatDate, StatusBadge, Loading, Failure } from "./components";
import { Workspace } from "../../app/Workspace";
import "./users.css";

function JsonView({
  value,
  label = "查看技术详情",
}: {
  value: unknown;
  label?: string;
}) {
  return (
    <details className="json-view">
      <summary>{label}</summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}

export function UserLayout() {
  const location = useLocation();
  const { userId = "" } = useParams();
  const query = useQuery({
    queryKey: ["user", userId],
    queryFn: () => api.user(userId),
    enabled: Boolean(userId),
  });
  if (query.isLoading)
    return (
      <div className="page">
        <Loading label="正在读取用户" />
      </div>
    );
  if (query.error || !query.data)
    return (
      <div className="page">
        <Failure error={query.error} />
      </div>
    );
  const user = query.data;
  if (/^\/users\/[^/]+(?:\/traces\/[^/]+)?\/?$/.test(location.pathname))
    return <Outlet context={{ user }} />;
  return (
    <Workspace title="用户管理 / 用户资料" note="敏感读取已审计">
      <div className="page">
        <Link to="/users" className="back-link">
          ← 返回用户列表
        </Link>
        <header className="user-hero">
          <div className="avatar avatar-large">
            {(user.nickname || "匿").slice(0, 1)}
          </div>
          <div>
            <p className="eyebrow">USER · {user.user_ref}</p>
            <h1>{user.nickname || "未命名用户"}</h1>
            <p>
              最近活跃 {formatDate(user.last_seen_at)} · 首次出现{" "}
              {formatDate(user.first_seen_at)}
            </p>
          </div>
        </header>
        <nav className="tabs">
          <NavLink end to={`/users/${userId}`}>
            输出链路
          </NavLink>
          <NavLink to={`/users/${userId}/memories`}>记忆</NavLink>
          <NavLink to={`/users/${userId}/records`}>健康记录</NavLink>
          <NavLink to={`/users/${userId}/routines`}>提醒日程</NavLink>
          <NavLink to={`/users/${userId}/usage`}>问答用量</NavLink>
        </nav>
        <Outlet context={{ user }} />
      </div>
    </Workspace>
  );
}

export function MemoriesPage() {
  const user = useUser();
  const query = useQuery({
    queryKey: ["memories", user.id],
    queryFn: () => api.memories(user.id),
  });
  if (query.isLoading) return <Loading />;
  if (query.error) return <Failure error={query.error} />;
  const memories = query.data ?? [];
  return (
    <section>
      <div className="section-heading">
        <div>
          <h2>用户记忆</h2>
          <p>
            数据库是事实依据；Mem0 只负责帮助找到相关资料，不会覆盖这里的值。
          </p>
        </div>
        <span>{memories.length} 条</span>
      </div>
      {memories.length === 0 ? (
        <div className="state-card">暂无长期记忆</div>
      ) : (
        <div className="memory-grid">
          {memories.map((memory) => (
            <MemoryCard key={memory.id} memory={memory} />
          ))}
        </div>
      )}
    </section>
  );
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
  const indexText =
    indexStatus === "completed"
      ? "已进入语义索引"
      : indexStatus === "not_queued"
        ? "语义索引未启用"
        : indexStatus === "failed"
          ? "语义索引同步失败"
          : "等待同步语义索引";
  return (
    <article className={`memory-card memory-${memory.status}`}>
      <header>
        <div>
          <span className="eyebrow">
            {memory.kind} · {memory.memory_key}
          </span>
          <h3>{MEMORY_LABELS[memory.memory_key] ?? memory.memory_key}</h3>
        </div>
        <StatusBadge value={memory.status} />
      </header>
      <p className="memory-value">{formatMemoryValue(memory)}</p>
      <dl className="memory-meta">
        <div>
          <dt>事实来源</dt>
          <dd>用户原话证据 · {shortId(memory.evidence_item_id)}</dd>
        </div>
        <div>
          <dt>生效时间</dt>
          <dd>{formatDate(memory.valid_from)}</dd>
        </div>
        <div>
          <dt>可信级别</dt>
          <dd>
            {memory.assertion === "user_explicit"
              ? "用户明确表达"
              : memory.assertion}
          </dd>
        </div>
        <div>
          <dt>召回索引</dt>
          <dd>
            {indexText}
            {memory.semantic_index.attempt_count
              ? ` · 尝试 ${memory.semantic_index.attempt_count} 次`
              : ""}
          </dd>
        </div>
      </dl>
      {memory.semantic_index.error_detail && (
        <div className="event-error">
          {memory.semantic_index.error_code} ·{" "}
          {memory.semantic_index.error_detail}
        </div>
      )}
      <JsonView value={memory} label="查看记忆技术详情" />
    </article>
  );
}

function shortId(value: string | null) {
  return value ? value.slice(0, 8) : "历史记录";
}

function formatMemoryValue(memory: MemoryRecord): string {
  const value = memory.value ?? {};
  if (
    memory.memory_key === "profile.height" &&
    typeof value.millimeters === "number"
  )
    return `${value.millimeters / 10} cm`;
  if (
    memory.memory_key === "goal.target_weight" &&
    typeof value.grams === "number"
  )
    return `${value.grams / 1000} kg`;
  if (
    memory.memory_key === "goal.target_body_fat" &&
    typeof value.basis_points === "number"
  )
    return `${value.basis_points / 100}%`;
  if (typeof value.statement === "string") return value.statement;
  if (typeof value.name === "string") return value.name;
  if (typeof value.item === "string")
    return `${value.item} · ${String(value.stance ?? "")}`;
  if (typeof value.activity === "string")
    return `${value.activity} · ${String(value.stance ?? "")}`;
  return JSON.stringify(value, null, 2);
}

export function RecordsPage() {
  const user = useUser();
  const query = useQuery({
    queryKey: ["records", user.id],
    queryFn: () => api.records(user.id),
  });
  if (query.isLoading) return <Loading />;
  if (query.error) return <Failure error={query.error} />;
  return (
    <>
      {Object.entries(query.data ?? {}).map(([name, items]) => (
        <DataCards
          key={name}
          title={
            {
              weights: "体重",
              body_fat: "体脂",
              meals: "饮食",
              exercises: "运动",
            }[name] ?? name
          }
          items={items}
          empty="暂无记录"
        />
      ))}
    </>
  );
}

export function RoutinesPage() {
  const user = useUser();
  const query = useQuery({
    queryKey: ["routines", user.id],
    queryFn: () => api.routines(user.id),
  });
  if (query.isLoading) return <Loading />;
  if (query.error) return <Failure error={query.error} />;
  return (
    <>
      <DataCards
        title="提醒设置"
        items={query.data?.preference ? [query.data.preference] : []}
        empty="未配置提醒"
      />
      <DataCards
        title="调度记录"
        items={query.data?.jobs ?? []}
        empty="暂无调度记录"
      />
    </>
  );
}

function DataCards({
  title,
  subtitle,
  items,
  empty,
}: {
  title: string;
  subtitle?: string;
  items: Array<Record<string, unknown>>;
  empty: string;
}) {
  return (
    <section>
      <div className="section-heading">
        <div>
          <h2>{title}</h2>
          {subtitle && <p>{subtitle}</p>}
        </div>
        <span>{items.length} 条</span>
      </div>
      {items.length === 0 ? (
        <div className="state-card">{empty}</div>
      ) : (
        <div className="data-grid">
          {items.map((item, index) => (
            <article className="data-card" key={String(item.id ?? index)}>
              <JsonView value={item} />
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
