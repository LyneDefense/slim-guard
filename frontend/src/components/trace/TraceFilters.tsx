import type { TraceListFilters } from "../../types";

const FILTER_KEYS = [
  "generation_status", "delivery_status", "mode", "agent_failure", "rag", "repair",
  "degraded", "graph_version", "agent_version", "profile_version",
] as const satisfies readonly (keyof TraceListFilters)[];

const SELECT_FILTERS: Array<{
  key: keyof TraceListFilters;
  label: string;
  options: Array<[string, string]>;
}> = [
  { key: "generation_status", label: "生成状态", options: [["succeeded", "成功"], ["waiting", "等待确认"], ["degraded", "降级"], ["failed", "失败"], ["unknown", "未知"], ["skipped", "跳过"]] },
  { key: "delivery_status", label: "投递状态", options: [["accepted", "已送达"], ["sending", "发送中"], ["pending_review", "待审核"], ["failed", "失败"], ["unknown", "未知"], ["deferred_external_session", "人工会话中"], ["skipped", "跳过"]] },
  { key: "mode", label: "运行模式", options: [["off", "Legacy / Off"], ["shadow", "Shadow"], ["canary", "Canary"], ["on", "On"]] },
  { key: "agent_failure", label: "Agent 失败", options: [["true", "有失败"], ["false", "无失败"]] },
  { key: "rag", label: "RAG", options: [["true", "已使用"], ["false", "未使用"]] },
  { key: "repair", label: "返回修复", options: [["true", "已修复"], ["false", "未修复"]] },
  { key: "degraded", label: "工作流降级", options: [["true", "已降级"], ["false", "未降级"]] },
];

export function traceFiltersFromParams(params: URLSearchParams): TraceListFilters {
  return Object.fromEntries(FILTER_KEYS.flatMap((key) => {
    const value = params.get(key)?.trim();
    return value ? [[key, value]] : [];
  }));
}

export function TraceFilters({
  params,
  onApply,
}: {
  params: URLSearchParams;
  onApply: (params: URLSearchParams) => void;
}) {
  const filters = traceFiltersFromParams(params);
  return (
    <form className="trace-filter-form" key={params.toString()} onSubmit={(event) => {
      event.preventDefault();
      const values = new FormData(event.currentTarget);
      const next = new URLSearchParams();
      for (const key of FILTER_KEYS) {
        const value = String(values.get(key) ?? "").trim();
        if (value) next.set(key, value);
      }
      onApply(next);
    }}>
      <div className="filters trace-filters">
        {SELECT_FILTERS.map(({ key, label, options }) => (
          <label key={key}>{label}<select name={key} defaultValue={filters[key] ?? ""}>
            <option value="">全部</option>
            {options.map(([value, title]) => <option value={value} key={value}>{title}</option>)}
          </select></label>
        ))}
      </div>
      <div className="filters trace-filters">
        {[["graph_version", "Graph 版本"], ["agent_version", "Agent 版本"], ["profile_version", "Profile 版本"]].map(([key, label]) => (
          <label key={key}>{label}<input name={key} defaultValue={filters[key as keyof TraceListFilters] ?? ""} placeholder="精确版本号" /></label>
        ))}
        <button type="submit">应用筛选</button>
        <button type="button" onClick={() => onApply(new URLSearchParams())}>清除筛选</button>
      </div>
    </form>
  );
}
