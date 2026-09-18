import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { stylesApi, type Case, type Review, type Style } from "./api";
export function ReviewPanel({ style }: { style: Style }) {
  const [params, setParams] = useSearchParams();
  const [offset, setOffset] = useState(0);
  const versions = useQuery({
    queryKey: ["expression-versions", style.id],
    queryFn: () => stylesApi.versions(style.id),
  });
  const available =
    versions.data?.items.filter((v) => v.review_summary.total > 0) ?? [];
  const version = params.get("version") ?? available[0]?.id ?? "";
  const cases = useQuery({
    queryKey: ["expression-cases", style.id, version, offset],
    queryFn: () => stylesApi.cases(style.id, version, offset),
    enabled: !!version,
  });
  const readOnly =
    available.find((v) => v.id === version)?.status !== "ready_for_review";
  return (
    <>
      <section className="expression-card">
        <h2>评审版本</h2>
        <p>
          这里评审独立测试的实际输出，不是重新审核原素材。接受不会把模型回答加入素材库。
        </p>
        <label>
          风格版本
          <select
            value={version}
            onChange={(e) => {
              setParams({ version: e.target.value });
              setOffset(0);
            }}
          >
            {available.map((v) => (
              <option key={v.id} value={v.id}>
                {v.name}
              </option>
            ))}
          </select>
        </label>
        {!available.length && <p>构建完成后，评审样例会出现在这里。</p>}
        {versions.error && <p role="alert">{versions.error.message}</p>}
        {cases.error && <p role="alert">{cases.error.message}</p>}
        {cases.data && (
          <div className="style-overview-grid">
            {[
              ["pending", "待评审"],
              ["accept", "已接受"],
              ["reject", "已拒绝"],
            ].map(([key, label]) => (
              <article key={key}>
                <strong>{cases.data.counts[key]}</strong>
                <span>{label}</span>
              </article>
            ))}
          </div>
        )}
      </section>
      {cases.data?.items.map((c) => (
        <ReviewCard
          key={`${c.id}:${JSON.stringify(c.review)}`}
          styleId={style.id}
          value={c}
          readOnly={readOnly}
        />
      ))}
      {cases.data && cases.data.total > 20 && (
        <div className="pager">
          <button
            disabled={!offset}
            onClick={() => setOffset(Math.max(0, offset - 20))}
          >
            上一页
          </button>
          <span>
            {offset + 1}–{Math.min(offset + 20, cases.data.total)} /{" "}
            {cases.data.total}
          </span>
          <button
            disabled={offset + 20 >= cases.data.total}
            onClick={() => setOffset(offset + 20)}
          >
            下一页
          </button>
        </div>
      )}
    </>
  );
}
function ReviewCard({
  styleId,
  value,
  readOnly,
}: {
  styleId: string;
  value: Case;
  readOnly: boolean;
}) {
  const client = useQueryClient();
  const [review, setReview] = useState<Review>(
    value.review ?? {
      style_match: 3,
      fidelity: 3,
      appropriateness: 3,
      decision: "accept",
      reason: "",
      desired_response: "",
    },
  );
  const [rejecting, setRejecting] = useState(false);
  const save = useMutation({
    mutationFn: (decision: "accept" | "reject") =>
      stylesApi.review(styleId, value.id, {
        ...review,
        decision,
        concern: decision === "accept" ? "output" : review.concern,
      }),
    onSuccess: () => {
      setRejecting(false);
      void client.invalidateQueries({
        queryKey: ["expression-cases", styleId],
      });
      void client.invalidateQueries({
        queryKey: ["expression-versions", styleId],
      });
      void client.invalidateQueries({ queryKey: ["expression-styles"] });
    },
  });
  return (
    <article className="expression-card">
      <header>
        <h2>回复评审</h2>
        <span>
          {value.review
            ? value.review.decision === "accept"
              ? "已接受"
              : "已拒绝"
            : "待评审"}
        </span>
      </header>
      <div className="expression-three">
        <section>
          <h3>用户输入</h3>
          <p>{value.user_input}</p>
        </section>
        <section>
          <h3>医生回答</h3>
          <p>{value.doctor_response}</p>
        </section>
        <section>
          <h3>期望医生回答</h3>
          <p>{review.desired_response || "尚未填写，可在下方补充"}</p>
        </section>
      </div>
      {!!value.test_case.context.length && (
        <p>已知语境：{value.test_case.context.join("；")}</p>
      )}
      {!value.automated.passed && (
        <p role="alert">
          自动审查未通过或使用了原稿兜底，不能作为风格成功发布。
        </p>
      )}
      {value.review?.concern && value.review.concern !== "output" && (
        <p role="alert">
          待复核：
          {value.review.concern === "test_case" ? "测试题或原稿" : "自动审查"}
          存在争议。本条不进入下一次风格素材或回归题，不覆盖自动结果。
        </p>
      )}
      <details>
        <summary>查看改写前原文及自动检查</summary>
        <p>{value.original_response}</p>
        <p>
          {value.automated.passed
            ? "共享审查通过"
            : `未通过：${value.automated.failure_code ?? "需要修订"}`}
        </p>
        <h3>对照版本回答</h3>
        <p>{value.baseline_response}</p>
        {value.automated.checks?.map((check) => (
          <section key={check.attempt}>
            <h4>
              第 {check.attempt} 次改写 · {check.passed ? "通过" : "未通过"}
            </h4>
            <p>{check.output}</p>
            <p>{check.issues.join("；")}</p>
          </section>
        ))}
      </details>
      <div className="expression-three">
        {(["style_match", "fidelity", "appropriateness"] as const).map(
          (key, i) => (
            <label key={key}>
              {["风格匹配", "语义忠实", "表达适宜"][i]}
              <select
                disabled={readOnly}
                value={review[key]}
                onChange={(e) =>
                  setReview({ ...review, [key]: Number(e.target.value) })
                }
              >
                {[1, 2, 3, 4, 5].map((n) => (
                  <option key={n} value={n}>
                    {n} / 5
                  </option>
                ))}
              </select>
            </label>
          ),
        )}
      </div>
      <label>
        补充期望回答（选填，作为下次构建反馈，不修改本版本）
        <textarea
          disabled={readOnly}
          value={review.desired_response}
          onChange={(e) =>
            setReview({ ...review, desired_response: e.target.value })
          }
          maxLength={4000}
        />
      </label>
      <label>
        评分说明（接受时选填）
        <textarea
          disabled={readOnly}
          value={review.reason}
          onChange={(e) => setReview({ ...review, reason: e.target.value })}
          maxLength={2000}
        />
      </label>
      {save.error && <p role="alert">{save.error.message}</p>}
      <div className="expression-actions">
        <button
          disabled={readOnly || save.isPending}
          onClick={() => save.mutate("accept")}
        >
          接受
        </button>
        <button
          className="danger"
          disabled={readOnly || save.isPending}
          onClick={() => setRejecting(true)}
        >
          拒绝
        </button>
      </div>
      {readOnly && <small>已发布版本仅供查看。</small>}
      {rejecting && (
        <div className="expression-modal-backdrop">
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby={`reject-${value.id}`}
            className="expression-card"
          >
            <h2 id={`reject-${value.id}`}>填写拒绝理由</h2>
            <label>
              问题出在哪里？
              <select
                value={review.concern ?? "output"}
                onChange={(e) =>
                  setReview({
                    ...review,
                    concern: e.target.value as Review["concern"],
                  })
                }
              >
                <option value="output">医生回答不合适</option>
                <option value="test_case">测试题或中性原稿有问题</option>
                <option value="automated_review">自动审查判决有问题</option>
              </select>
            </label>
            <textarea
              autoFocus
              aria-label="拒绝理由"
              value={review.reason}
              maxLength={2000}
              onChange={(e) => setReview({ ...review, reason: e.target.value })}
            />
            {save.error && <p role="alert">{save.error.message}</p>}
            <button
              disabled={!review.reason.trim() || save.isPending}
              onClick={() => save.mutate("reject")}
            >
              确认拒绝
            </button>
            <button
              disabled={save.isPending}
              onClick={() => setRejecting(false)}
            >
              取消
            </button>
          </section>
        </div>
      )}
    </article>
  );
}
