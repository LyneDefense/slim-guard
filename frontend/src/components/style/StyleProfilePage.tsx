import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, NavLink, useParams } from "react-router-dom";
import { stylesApi } from "./api";
import { BuildPanel } from "./BuildPanel";
import { ReviewPanel } from "./ReviewPanel";
import { CorpusPanel } from "./CorpusPanel";
import "./style.css";

export function StyleProfilePage() {
  const { styleId, tab = "build" } = useParams();
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ["expression-styles"],
    queryFn: stylesApi.list,
  });
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [adding, setAdding] = useState(false);
  const create = useMutation({
    mutationFn: () => stylesApi.create(name, description),
    onSuccess: () => {
      setAdding(false);
      setName("");
      setDescription("");
      void client.invalidateQueries({ queryKey: ["expression-styles"] });
    },
  });
  if (query.isLoading) return <div className="state-card">正在加载风格…</div>;
  if (query.error)
    return <div className="state-card state-error">{query.error.message}</div>;
  const styles = query.data?.items ?? [];
  const style = styles.find((s) => s.id === styleId);
  if (styleId && !style)
    return (
      <div className="state-card">
        风格不存在。<Link to="/styles">返回风格列表</Link>
      </div>
    );
  if (!style)
    return (
      <div className="page expression-page">
        <header className="page-header">
          <div>
            <p className="eyebrow">EXPRESSION STYLES</p>
            <h1>表达风格</h1>
            <p>选择风格，管理版本与表达示例。</p>
          </div>
        </header>
        <div className="style-overview-grid">
          <article>
            <span>风格总数</span>
            <strong>{styles.length}</strong>
          </article>
          <article>
            <span>系统默认</span>
            <strong>
              {styles.find((s) => s.is_default)?.name ?? "未设置"}
            </strong>
          </article>
          <article>
            <span>示例总数</span>
            <strong>{styles.reduce((n, s) => n + s.example_count, 0)}</strong>
          </article>
        </div>
        <h2>风格列表</h2>
        <div className="style-profile-grid">
          {styles.map((s) => (
            <Link
              className="style-profile-card"
              to={`/styles/${encodeURIComponent(s.id)}/build`}
              key={s.id}
            >
              <h2>{s.name}</h2>
              <span className="status-chip success">
                {s.is_default ? "系统默认 · " : ""}
                {s.active_version_id ? "已启用" : "尚未启用"}
              </span>
              <p>{s.description}</p>
              <footer>
                <span>{s.example_count} 条示例</span>
                <span>{s.active_version ?? "尚无版本"}</span>
              </footer>
            </Link>
          ))}
          <button
            className="style-profile-card style-profile-empty"
            onClick={() => setAdding(true)}
          >
            ＋ 新建风格
          </button>
        </div>
        {adding && (
          <form
            className="expression-card"
            onSubmit={(e) => {
              e.preventDefault();
              create.mutate();
            }}
          >
            <h2>新建风格</h2>
            <label>
              风格名称
              <input
                value={name}
                maxLength={100}
                required
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <label>
              描述
              <textarea
                value={description}
                maxLength={1000}
                onChange={(e) => setDescription(e.target.value)}
              />
            </label>
            {create.error && <p role="alert">{create.error.message}</p>}
            <button disabled={create.isPending || !name.trim()}>创建</button>
            <button type="button" onClick={() => setAdding(false)}>
              取消
            </button>
          </form>
        )}
      </div>
    );
  return (
    <div className="page expression-page">
      <div className="breadcrumb">
        <Link to="/styles">表达风格</Link>
        <span>/</span>
        <strong>{style.name}</strong>
      </div>
      <header className="style-profile-header">
        <div>
          <h1>{style.name}</h1>
          <span className="status-chip success">
            {style.is_default ? "系统默认 · " : ""}
            {style.active_version_id ? "已启用" : "尚未启用"}
          </span>
          <p>{style.description}</p>
        </div>
      </header>
      <nav className="style-detail-tabs">
        {[
          ["build", "构建版本"],
          ["review", "评审版本"],
          ["corrections", "追加纠正素材"],
        ].map(([key, label]) => (
          <NavLink
            key={key}
            to={`/styles/${style.id}/${key}`}
            className={({ isActive }) => (isActive ? "active" : "")}
          >
            {label}
          </NavLink>
        ))}
      </nav>
      {tab === "review" ? (
        <ReviewPanel key={style.id} style={style} />
      ) : tab === "corrections" ? (
        <CorpusPanel key={style.id} style={style} />
      ) : (
        <BuildPanel key={style.id} style={style} />
      )}
    </div>
  );
}
