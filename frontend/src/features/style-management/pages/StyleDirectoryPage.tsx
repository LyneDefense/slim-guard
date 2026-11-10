import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { styleStudioApi } from "../api";
import { Dialog } from "../components/Dialog";
import "../studio.css";
export function StyleDirectoryPage() {
  const query = useQuery({
    queryKey: ["expression-styles"],
    queryFn: styleStudioApi.list,
  });
  const client = useQueryClient();
  const navigate = useNavigate();
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const create = useMutation({
    mutationFn: () => styleStudioApi.create(name.trim(), description.trim()),
    onSuccess: (style) => {
      void client.invalidateQueries({ queryKey: ["expression-styles"] });
      navigate(`/styles/${encodeURIComponent(style.id)}`);
    },
  });
  return (
    <div className="page style-studio">
      <header className="page-header">
        <div>
          <p className="eyebrow">EXPRESSION STYLES</p>
          <h1>表达风格</h1>
          <p>编辑角色设定，用实际对话试一试，再发布并启用。</p>
        </div>
        <button
          className="studio-primary"
          onClick={() => {
            create.reset();
            setAdding(true);
          }}
        >
          ＋ 新建风格
        </button>
      </header>
      {query.isLoading && (
        <div className="state-card" role="status">
          正在加载表达风格…
        </div>
      )}
      {query.error && (
        <div className="state-card state-error" role="alert">
          {query.error.message}
          <button onClick={() => void query.refetch()}>重试</button>
        </div>
      )}
      <h2>风格列表</h2>
      <div className="studio-directory">
        {query.data?.items.map((style) => (
          <Link
            className="studio-style-card"
            key={style.id}
            to={`/styles/${encodeURIComponent(style.id)}`}
          >
            <header>
              <h2>{style.name}</h2>
              <span
                className={`status ${style.active_version_id ? "status-good" : ""}`}
              >
                {style.is_default ? "系统默认 · " : ""}
                {style.active_version_id ? "已启用" : "尚未启用"}
              </span>
            </header>
            <p>{style.description || "打开工作台，填写角色设定和对话示例。"}</p>
            <footer>
              <span>{style.example_count} 条示例</span>
              <span>{style.active_version || "尚无发布版本"}</span>
            </footer>
          </Link>
        ))}
      </div>
      {query.data?.items.length === 0 && (
        <div className="state-card">还没有表达风格，先新建一个角色。</div>
      )}
      {adding && (
        <Dialog
          title="新建表达风格"
          onClose={() => {
            if (!create.isPending) setAdding(false);
          }}
        >
          <form
            onSubmit={(event) => {
              event.preventDefault();
              create.mutate();
            }}
          >
            <div className="studio-dialog-body">
              <label className="studio-field">
                风格名称
                <input
                  value={name}
                  maxLength={100}
                  required
                  onChange={(event) => setName(event.target.value)}
                />
              </label>
              <label className="studio-field">
                描述（可选）
                <textarea
                  value={description}
                  maxLength={1000}
                  onChange={(event) => setDescription(event.target.value)}
                />
              </label>
              {create.error && (
                <p className="alert" role="alert">
                  {create.error.message}
                </p>
              )}
            </div>
            <footer className="studio-dialog-actions">
              <button
                type="button"
                disabled={create.isPending}
                onClick={() => setAdding(false)}
              >
                取消
              </button>
              <button
                className="studio-primary"
                disabled={create.isPending || !name.trim()}
              >
                {create.isPending ? "正在创建…" : "创建并编辑"}
              </button>
            </footer>
          </form>
        </Dialog>
      )}
    </div>
  );
}
