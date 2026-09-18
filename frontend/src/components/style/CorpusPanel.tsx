import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { stylesApi, type Style } from "./api";
import { useSearchParams } from "react-router-dom";
const categories: Record<string, string> = {
  unclassified: "待分析",
  expression: "纯表达差异",
  content_change: "内容变化",
  conflict: "冲突素材",
  unusable: "不可用素材",
};
const statuses: Record<string, string> = {
  pending: "待构建",
  approved: "可用",
  excluded: "已排除",
  conflict: "待解决冲突",
};
export function CorpusPanel({ style }: { style: Style }) {
  const [params] = useSearchParams();
  const client = useQueryClient();
  const [form, setForm] = useState({
    user_input: "",
    original_response: "",
    desired_response: "",
  });
  const [filters, setFilters] = useState({
    q: params.get("q") ?? "",
    category: "",
    source: "",
    status: "",
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
  const add = useMutation({
    mutationFn: () => stylesApi.append(style.id, form),
    onSuccess: () => {
      setForm({ user_input: "", original_response: "", desired_response: "" });
      setFilters({ ...filters, offset: "0" });
      refresh();
    },
  });
  const change = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      stylesApi.state(style.id, id, status),
    onSuccess: refresh,
  });
  const filter = (key: string, value: string) =>
    setFilters({ ...filters, [key]: value, offset: "0" });
  return (
    <>
      <form
        className="expression-card"
        onSubmit={(e) => {
          e.preventDefault();
          add.mutate();
        }}
      >
        <h2>追加纠正素材</h2>
        <p>加入当前风格的完整示例库，下次构建时使用。不会修改已启用版本。</p>
        <div className="expression-three">
          {(
            ["user_input", "original_response", "desired_response"] as const
          ).map((key, i) => (
            <label key={key}>
              {["用户输入", "医生回答", "期望医生回答"][i]}
              <textarea
                value={form[key]}
                required
                maxLength={4000}
                onChange={(e) => setForm({ ...form, [key]: e.target.value })}
              />
            </label>
          ))}
        </div>
        <button
          disabled={add.isPending || Object.values(form).some((v) => !v.trim())}
        >
          加入示例库
        </button>
        {add.isSuccess && <p role="status">已加入示例库。</p>}
        {add.error && <p role="alert">{add.error.message}</p>}
      </form>
      <section className="expression-card">
        <header>
          <h2>全部示例</h2>
          <span>{query.data?.total ?? 0} 条</span>
        </header>
        <p>
          分类描述素材质量与表达差异，仅供整理筛选，不用于意图路由或套用回复。
        </p>
        <div className="expression-filters">
          <label>
            搜索
            <input
              value={filters.q}
              onChange={(e) => filter("q", e.target.value)}
              placeholder="用户输入或回答"
            />
          </label>
          <label>
            素材分类
            <select
              value={filters.category}
              onChange={(e) => filter("category", e.target.value)}
            >
              <option value="">全部</option>
              {Object.entries(categories).map(([key, label]) => (
                <option value={key} key={key}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            来源
            <select
              value={filters.source}
              onChange={(e) => filter("source", e.target.value)}
            >
              <option value="">全部</option>
              <option value="correction">纠正素材</option>
              <option value="review">评审纠正</option>
              <option value="accepted_review">评审接受</option>
            </select>
          </label>
          <label>
            状态
            <select
              value={filters.status}
              onChange={(e) => filter("status", e.target.value)}
            >
              <option value="">全部</option>
              {Object.entries(statuses).map(([key, label]) => (
                <option value={key} key={key}>
                  {label}
                </option>
              ))}
            </select>
          </label>
        </div>
        {query.isLoading && <p>正在加载示例…</p>}
        {query.error && <p role="alert">{query.error.message}</p>}
        {change.error && <p role="alert">{change.error.message}</p>}
        {query.data?.total === 0 && <p>当前条件下没有示例。</p>}
        {query.data?.items.map((e) => (
          <article className="expression-example" key={e.id}>
            <header>
              <span>
                {categories[e.category] ?? e.category} ·{" "}
                {statuses[e.status] ?? e.status} ·{" "}
                {e.source === "accepted_review"
                  ? "评审接受"
                  : e.source === "review"
                    ? "评审纠正"
                    : "纠正素材"}
              </span>
              <small>
                {e.actor} · {new Date(e.created_at).toLocaleString("zh-CN")}
              </small>
            </header>
            <div className="expression-three">
              <section>
                <h3>用户输入</h3>
                <p>{e.user_input}</p>
              </section>
              <section>
                <h3>医生回答</h3>
                <p>{e.original_response}</p>
              </section>
              <section>
                <h3>期望医生回答</h3>
                <p>{e.desired_response}</p>
              </section>
            </div>
            {e.reason && <p>整理说明：{e.reason}</p>}
            <footer>
              <span>
                {e.included_versions.length
                  ? `已纳入：${e.included_versions.join("、")}`
                  : "尚未纳入构建"}
              </span>
              <button
                disabled={change.isPending}
                onClick={() =>
                  change.mutate({
                    id: e.id,
                    status: e.status === "excluded" ? "pending" : "excluded",
                  })
                }
              >
                {e.status === "excluded" ? "重新纳入构建" : "排除素材"}
              </button>
            </footer>
          </article>
        ))}
        <div className="pager">
          <button
            disabled={Number(filters.offset) === 0}
            onClick={() =>
              setFilters({
                ...filters,
                offset: String(Math.max(0, Number(filters.offset) - 20)),
              })
            }
          >
            上一页
          </button>
          <span>
            第 {Math.floor(Number(filters.offset) / 20) + 1} 页 · 共{" "}
            {query.data?.total ?? 0} 条
          </span>
          <button
            disabled={Number(filters.offset) + 20 >= (query.data?.total ?? 0)}
            onClick={() =>
              setFilters({
                ...filters,
                offset: String(Number(filters.offset) + 20),
              })
            }
          >
            下一页
          </button>
        </div>
      </section>
    </>
  );
}
