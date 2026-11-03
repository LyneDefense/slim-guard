import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Link,
  useLocation,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { callApi } from "./api";
import { Badge, CallState } from "./components";
import { date, elapsed } from "./presentation";
import { Workspace } from "../../app/Workspace";
import "./calls.css";

export function CallListPage() {
  const { userId } = useParams();
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const [draft, setDraft] = useState(params.get("search") || "");
  const offset = Math.max(0, Number(params.get("offset")) || 0);
  const query = useQuery({
    queryKey: ["call-list", userId || "all", params.toString()],
    queryFn: () => callApi.list(params, userId),
    refetchInterval: 5000,
  });
  const patch = (values: Record<string, string>) => {
    const next = new URLSearchParams(params);
    next.delete("offset");
    for (const [key, value] of Object.entries(values))
      value ? next.set(key, value) : next.delete(key);
    setParams(next);
  };
  const paginate = (amount: number) => {
    const next = new URLSearchParams(params);
    next.set("offset", String(Math.max(0, offset + amount)));
    setParams(next);
  };
  return (
    <Workspace
      className="call-workspace"
      title={<>调用过程{userId ? " / 当前用户" : ""}</>}
      note="每 5 秒自动更新"
    >
      <div className="call-main">
        <div className="call-heading">
          <div>
            <h1>调用过程</h1>
            <small>按用户每一轮消息查看，而不是按日志行查看</small>
          </div>
          <div className="call-actions">
            <Link className="call-button" to="/users/directory">
              用户资料
            </Link>
            {userId && (
              <Link className="call-button" to="/users">
                查看所有用户
              </Link>
            )}
          </div>
        </div>
        {userId && (
          <nav className="call-user-links" aria-label="当前用户管理">
            <Link to={`/users/${userId}/memories`}>记忆</Link>
            <Link to={`/users/${userId}/records`}>健康记录</Link>
            <Link to={`/users/${userId}/routines`}>提醒日程</Link>
            <Link to={`/users/${userId}/usage`}>问答用量</Link>
          </nav>
        )}
        <form
          className="call-actions call-list-filters"
          onSubmit={(e) => {
            e.preventDefault();
            patch({ search: draft.trim() });
          }}
        >
          <input
            className="call-input"
            aria-label="搜索消息、用户或 Trace ID"
            placeholder="消息 / 用户 / Trace ID"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
          <button className="call-button" type="submit">
            搜索
          </button>
          <select
            className="call-select"
            aria-label="处理结果"
            value={params.get("result") || "all"}
            onChange={(e) => patch({ result: e.target.value })}
          >
            <option value="all">全部结果</option>
            <option value="issues">只看异常或修复</option>
            <option value="running">处理中</option>
          </select>
          <select
            className="call-select"
            aria-label="时间范围"
            value={params.has("start") ? params.get("days") || "7" : "all"}
            onChange={(e) => {
              const days = e.target.value;
              patch({
                days: days === "all" ? "" : days,
                start:
                  days === "all"
                    ? ""
                    : new Date(
                        Date.now() - Number(days) * 86400000,
                      ).toISOString(),
                end: "",
              });
            }}
          >
            <option value="all">全部时间</option>
            <option value="1">近 24 小时</option>
            <option value="7">近 7 天</option>
            <option value="30">近 30 天</option>
          </select>
          {params.toString() && (
            <button
              className="call-button"
              type="button"
              onClick={() => {
                setDraft("");
                setParams({});
              }}
            >
              清除筛选
            </button>
          )}
        </form>
        {query.isPending && <CallState loading />}
        {query.isError && (
          <CallState
            error={query.error}
            retry={() => {
              void query.refetch();
            }}
          />
        )}
        {query.data && (
          <>
            <section className="call-panel call-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>用户消息</th>
                    <th>服务耗时</th>
                    <th>模型 / 工具</th>
                    <th>业务 / 回复</th>
                    <th>结果</th>
                    <th>详情</th>
                  </tr>
                </thead>
                <tbody>
                  {query.data.items.map((row) => {
                    const back = `${location.pathname}${location.search}`;
                    const detail = `/users/${row.user_id}/traces/${row.id}?return=${encodeURIComponent(back)}`;
                    return (
                      <tr key={row.id}>
                        <td>
                          <Link to={`/users/${row.user_id}`}>
                            {row.nickname || row.user_id.slice(0, 8)}
                          </Link>
                          <small className="call-list-input">
                            {date(row.created_at)}
                          </small>
                          <span className="call-list-input">
                            {row.input_text ||
                              (row.trigger_type === "user_message"
                                ? "正文未记录／已脱敏"
                                : `系统触发 · ${row.trigger_type}`)}
                            {row.image_count > 0 &&
                              ` · 图片 × ${row.image_count}`}
                          </span>
                          <code className="call-trace-id">{row.id}</code>
                        </td>
                        <td>{elapsed(row.duration_ms)}</td>
                        <td>
                          {row.process.model_calls ?? "未知"} /{" "}
                          {row.process.tool_calls ?? "未知"}
                        </td>
                        <td>
                          <Badge value={row.process.business_status} />
                          <br />
                          <span className="call-muted">
                            医生 <Badge value={row.process.reply_status} />
                          </span>
                        </td>
                        <td>
                          <Badge value={row.process.outcome} />
                        </td>
                        <td>
                          <Link className="call-button" to={detail}>
                            查看 →
                          </Link>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {query.data.items.length === 0 && (
                <div className="call-empty">没有符合当前筛选条件的调用</div>
              )}
            </section>
            <div className="call-pagination">
              <button
                className="call-button"
                disabled={offset === 0}
                onClick={() => paginate(-30)}
              >
                上一页
              </button>
              <span>
                {query.data.items.length ? offset + 1 : 0}–
                {Math.min(offset + query.data.items.length, query.data.total)} /{" "}
                {query.data.total}
              </span>
              <button
                className="call-button"
                disabled={offset + 30 >= query.data.total}
                onClick={() => paginate(30)}
              >
                下一页
              </button>
            </div>
          </>
        )}
      </div>
    </Workspace>
  );
}
