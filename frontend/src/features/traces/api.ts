import { request } from "../../api";
import type { CallDetail, CallPage } from "./types";

export const callApi = {
  list: (params: URLSearchParams, userId: string) => {
    if (!userId) throw new Error("请先选择用户");
    const query = new URLSearchParams();
    for (const key of ["search", "result", "start", "end", "offset"]) {
      const value = params.get(key);
      if (value) query.set(key, value);
    }
    query.set("user_id", userId);
    query.set("limit", "30");
    return request<CallPage>(`/calls?${query}`);
  },
  detail: (userId: string, traceId: string) =>
    request<CallDetail>(
      `/calls/${encodeURIComponent(userId)}/traces/${encodeURIComponent(traceId)}`,
    ),
  image: (userId: string, traceId: string, assetId: string) =>
    `/api/admin/calls/${encodeURIComponent(userId)}/traces/${encodeURIComponent(traceId)}/images/${encodeURIComponent(assetId)}`,
};
