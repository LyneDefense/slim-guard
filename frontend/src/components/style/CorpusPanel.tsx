import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { stylesApi, type Style, type MaterialInput } from "./api";

import { materialRoles as roles } from "./labels";
const empty: MaterialInput = {
  user_input: "",
  original_response: "",
  desired_response: "",
  correction_opinion: "",
};
export function CorpusPanel({ style }: { style: Style }) {
  const [params] = useSearchParams();
  const client = useQueryClient();
  const [form, setForm] = useState<MaterialInput>(empty);
  const [editing, setEditing] = useState<string | null>(null);
  const [filters, setFilters] = useState({
    q: params.get("q") ?? "",
    role: "",
    participation: "unused",
    offset: "0",
    limit: "20",
  });
  const query = useQuery({
    queryKey: ["expression-examples", style.id, filters],
    queryFn: () => stylesApi.examples(style.id, filters),
  });
  const refresh = () => {
    void client.invalidateQueries({
      queryKey: ["expression-examples", style.id],
    });
    void client.invalidateQueries({ queryKey: ["expression-styles"] });
  };
  const save = useMutation({
    mutationFn: () =>
      editing
        ? stylesApi.edit(style.id, editing, form)
        : stylesApi.append(style.id, form),
    onSuccess: () => {
      setForm(empty);
      setEditing(null);
      setFilters({ ...filters, participation: "unused", offset: "0" });
      refresh();
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => stylesApi.remove(style.id, id),
    onSuccess: refresh,
  });
  const valid =
    form.user_input.trim() &&
    form.original_response.trim() &&
    (form.desired_response.trim() || form.correction_opinion.trim());
  const offset = Number(filters.offset);
  return (
    <>
      <form
        className="expression-card"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <h2>{editing ? "修改素材（生成新修订）" : "追加纠正素材"}</h2>
        <p>
          纯人工填写。下次构建会分析两个 Tab 的全部素材，不立即影响线上风格。
        </p>
        <div className="expression-columns">
          {(
            [
              "user_input",
              "original_response",
              "desired_response",
              "correction_opinion",
            ] as const
          ).map((key, i) => (
            <label key={key}>
              {
                [
                  "用户输入（必填）",
                  "已经发生的医生回答（必填）",
                  "期望医生回答",
                  "纠正意见",
                ][i]
              }
              <textarea
                required={i < 2}
                maxLength={i === 3 ? 2000 : 4000}
                value={form[key]}
                onChange={(e) => setForm({ ...form, [key]: e.target.value })}
              />
            </label>
          ))}
        </div>
        <p>
          期望回答与纠正意见至少填一项；只有负面意见也能提交，训练时会判断适用方式。
        </p>
        <button disabled={save.isPending || !valid}>
          {editing ? "保存新修订" : "加入示例库"}
        </button>
        {editing && (
          <button
            type="button"
            onClick={() => {
              setEditing(null);
              setForm(empty);
            }}
          >
            取消修改
          </button>
        )}
        {save.error && <p role="alert">{save.error.message}</p>}
        {save.isSuccess && <p role="status">已保存，当前修订尚未参与构建。</p>}
      </form>
      <section className="expression-card">
        <h2>全部素材</h2>
        <p>
          “已参与”不等于被选作示例或已经上线。排除与冲突项仍参与下次全库分析。
        </p>
        <div
          className="style-detail-tabs"
          role="tablist"
          aria-label="素材参与状态"
        >
          {[
            ["unused", "未参与构建"],
            ["used", "已参与构建"],
          ].map(([key, label]) => (
            <button
              key={key}
              role="tab"
              aria-selected={filters.participation === key}
              className={filters.participation === key ? "active" : ""}
              onClick={() =>
                setFilters({ ...filters, participation: key, offset: "0" })
              }
            >
              {label}
            </button>
          ))}
        </div>
        <div className="expression-columns">
          <label>
            搜索素材
            <input
              value={filters.q}
              onChange={(e) =>
                setFilters({ ...filters, q: e.target.value, offset: "0" })
              }
            />
          </label>
          <label>
            处理结果
            <select
              value={filters.role}
              onChange={(e) =>
                setFilters({ ...filters, role: e.target.value, offset: "0" })
              }
            >
              <option value="">全部</option>
              {Object.entries(roles).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </label>
        </div>
        {query.isLoading && <p>正在加载素材…</p>}
        {query.error && <p role="alert">{query.error.message}</p>}
        {remove.error && <p role="alert">{remove.error.message}</p>}
        {query.data?.total === 0 && <p>此分组暂无素材。</p>}
        {query.data?.items.map((item) => (
          <article className="style-material" key={item.id}>
            <header>
              <strong>
                修订 {item.revision} ·{" "}
                {roles[item.last_result.role ?? ""] ?? "尚未分析"}
              </strong>
              <span>
                {item.actor} · {item.source}
              </span>
            </header>
            <div className="expression-three">
              <section>
                <h3>用户输入</h3>
                <p>{item.user_input}</p>
              </section>
              <section>
                <h3>医生回答</h3>
                <p>{item.original_response}</p>
              </section>
              <section>
                <h3>期望医生回答</h3>
                <p>{item.desired_response || "未提供，仅有纠正意见"}</p>
              </section>
            </div>
            {item.correction_opinion && (
              <p>纠正意见：{item.correction_opinion}</p>
            )}
            {item.last_result.reason && (
              <p>处理原因：{item.last_result.reason}</p>
            )}
            {item.last_result.selected_example && (
              <span className="status-chip success">固定示例</span>
            )}
            {item.last_result.used_for_rule && (
              <span className="status-chip success">支持 Guide</span>
            )}
            <div className="expression-actions">
              {item.last_run_id && (
                <Link to={`/styles/${style.id}/build?run=${item.last_run_id}`}>
                  查看关联构建
                </Link>
              )}
              <button
                onClick={() => {
                  setEditing(item.id);
                  setForm({
                    user_input: item.user_input,
                    original_response: item.original_response,
                    desired_response: item.desired_response,
                    correction_opinion: item.correction_opinion,
                  });
                  window.scrollTo({ top: 0, behavior: "smooth" });
                }}
              >
                修改素材
              </button>
              <button
                disabled={remove.isPending}
                onClick={() => {
                  if (
                    window.confirm(
                      "删除这条素材？后续构建不再使用，已有冻结版本不受影响。",
                    )
                  )
                    remove.mutate(item.id);
                }}
              >
                删除
              </button>
            </div>
          </article>
        ))}
        {query.data && (
          <div className="pager">
            <button
              disabled={!offset}
              onClick={() =>
                setFilters({
                  ...filters,
                  offset: String(Math.max(0, offset - 20)),
                })
              }
            >
              上一页
            </button>
            <span>
              共 {query.data.total} 条 · 第 {Math.floor(offset / 20) + 1} 页
            </span>
            <button
              disabled={offset + 20 >= query.data.total}
              onClick={() =>
                setFilters({ ...filters, offset: String(offset + 20) })
              }
            >
              下一页
            </button>
          </div>
        )}
      </section>
    </>
  );
}
