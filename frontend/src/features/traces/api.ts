import { request } from "../../api";
import type { CallDetail, CallPage } from "./types";

export const callApi = {
  list: (params: URLSearchParams, userId?: string) => {
    const query = new URLSearchParams();
    for (const key of ["search", "result", "start", "end", "offset"]) {
      const value = params.get(key);
      if (value) query.set(key, value);
    }
    if (userId || params.get("user_id"))
      query.set("user_id", userId || params.get("user_id")!);
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
