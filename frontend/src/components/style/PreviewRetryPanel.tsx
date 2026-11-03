import { useCallback, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { stylesApi, type Version } from "./api";

export function PreviewRetryPanel({ styleId, version }: { styleId: string; version: Version }) {
  const client = useQueryClient();
  const [created, setCreated] = useState("");
  const runId = created || version.preview_retry?.id || "";
  useEffect(() => {
    if (created && version.preview_retry?.id === created) setCreated("");
  }, [created, version.preview_retry?.id]);
  const job = useQuery({
    queryKey: ["build-detail", styleId, runId],
    queryFn: () => stylesApi.buildDetail(styleId, runId),
    enabled: !!runId,
    refetchInterval: query => ["queued", "running"].includes(query.state.data?.status ?? "queued") ? 2500 : false,
  });
  const status = job.data?.status ?? version.preview_retry?.status;
  const busy = !!runId && (!status || ["queued", "running"].includes(status));
  const refresh = useCallback(() => {
    void client.invalidateQueries({ queryKey: ["expression-versions", styleId] });
    void client.invalidateQueries({ queryKey: ["expression-cases", styleId, version.id] });
    void client.invalidateQueries({ queryKey: ["expression-builds", styleId] });
  }, [client, styleId, version.id]);
  useEffect(() => {
    if (status && !["queued", "running"].includes(status)) refresh();
  }, [runId, status, refresh]);
  const retry = useMutation({
    mutationFn: () => stylesApi.retryFailed(styleId, version.id, crypto.randomUUID()),
    onSuccess: run => {
      client.setQueryData(["build-detail", styleId, run.id], run);
      setCreated(run.id);
      refresh();
    },
  });
  if (version.status !== "ready_for_review") return null;
  const failed = version.review_summary.auto_failed;
  if (!failed && !runId) return null;
  return <section className="preview-retry">
    {!!failed && <p role="alert">
      仍有 {failed} 条自动检查未通过，人工接受不会覆盖失败结果。
    </p>}
    {!!failed && <>
      <p>仅重试失败用例，不修改风格包；其他有效评审保留。重试后的回答需要重新确认，旧回答与评审仍可追溯。</p>
      <button
        disabled={retry.isPending || busy || !version.role_policy_current}
        onClick={() => {
          if (window.confirm(`仅重试 ${failed} 条失败用例？这几条的新回答需要重新评审，其他评审保留。`)) retry.mutate();
        }}
      >{busy ? "失败用例重试中…" : `仅重试失败的 ${failed} 条`}</button>
    </>}
    {busy && <p role="status">只重新执行失败用例，原回答在重试完成前保持不变。</p>}
    {runId && <Link to={`/styles/${styleId}/build?run=${encodeURIComponent(runId)}`}>查看重试进度与历史结果</Link>}
    {status === "completed" && <p role="status">本次重试已完成，请确认新回答；若仍有失败，可查看具体原因。</p>}
    {status === "cancelled" && <p>本次重试已取消，原回答和评审保持不变。</p>}
    {(retry.error || job.error || job.data?.error) && <p role="alert">
      {retry.error?.message ?? job.error?.message ?? job.data?.error}
    </p>}
  </section>;
}
