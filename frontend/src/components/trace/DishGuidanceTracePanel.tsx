import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../../api";
import type { TraceAgentArtifact } from "../../types";
import type { WorkflowTraceView } from "./model";

type JsonObject = Record<string, unknown>;

export function DishGuidanceTracePanel({
  workflow,
  userId,
  traceId,
}: {
  workflow: WorkflowTraceView;
  userId: string;
  traceId: string;
}) {
  const recognition = latestArtifact(workflow.artifacts, "dishrecognition");
  const confirmed = latestArtifact(workflow.artifacts, "confirmeddishset");
  const evidence = latestArtifact(workflow.artifacts, "dishevidencebundle");
  const guidance = latestArtifact(workflow.artifacts, "dietguidanceassessment");
  if (!recognition && !confirmed && !evidence && !guidance) return null;

  return (
    <section className="dish-guidance-panel">
      <div className="section-heading">
        <div>
          <h2>菜品识别与饮食建议</h2>
          <p>按“识别 → 用户确认 → 证据检索 → 建议”核对；人工更正只追加审计记录，不会覆盖原结果。</p>
        </div>
        <span>{workflow.mode}</span>
      </div>
      <div className="dish-guidance-grid">
        <RecognitionCard artifact={recognition} />
        <ConfirmedCard artifact={confirmed} />
        <EvidenceCard artifact={evidence} />
        <GuidanceCard artifact={guidance} />
      </div>
      {recognition && (
        <CorrectionForm
          artifact={recognition}
          corrections={workflow.artifacts.filter((item) =>
            normalizeType(item.artifact_type) === "dishrecognitioncorrection"
              && item.parent_artifact_ids.includes(recognition.artifact_id)
          )}
          traceId={traceId}
          userId={userId}
        />
      )}
    </section>
  );
}

function RecognitionCard({ artifact }: { artifact: TraceAgentArtifact | null }) {
  const payload = artifactPayload(artifact);
  const dishes = objectArray(payload.dishes);
  return (
    <article className="dish-trace-card">
      <header><span>1</span><div><h3>菜品识别</h3><small>{artifact?.artifact_id ?? "未运行"}</small></div></header>
      {!artifact ? <p>本轮未使用图片识别。</p> : <>
        <p>{stringValue(payload.image_kind) ?? "未知图片类型"} · {
          payload.overall_requires_confirmation ? "需要确认" : "已达到自动采用条件"
        }</p>
        {dishes.map((dish) => (
          <div className="dish-trace-item" key={stringValue(dish.dish_ref) ?? JSON.stringify(dish)}>
            <strong>{stringValue(dish.dish_ref) ?? "未编号菜品"}</strong>
            <span>{objectArray(dish.candidates).map((candidate) => {
              const confidence = numberValue(candidate.confidence);
              return `${stringValue(candidate.label) ?? "未知"}${confidence === null ? "" : ` ${Math.round(confidence * 100)}%`}`;
            }).join(" / ") || "无候选"}</span>
            {stringArray(dish.uncertainty_reasons).length > 0
              && <small>{stringArray(dish.uncertainty_reasons).join("；")}</small>}
          </div>
        ))}
      </>}
    </article>
  );
}

function ConfirmedCard({ artifact }: { artifact: TraceAgentArtifact | null }) {
  const dishes = objectArray(artifactPayload(artifact).dishes);
  return (
    <article className="dish-trace-card">
      <header><span>2</span><div><h3>确认结果</h3><small>{artifact?.artifact_id ?? "等待中"}</small></div></header>
      {dishes.length === 0 ? <p>尚未形成 ConfirmedDishSet。</p> : dishes.map((dish) => (
        <div className="dish-trace-item" key={stringValue(dish.dish_ref) ?? JSON.stringify(dish)}>
          <strong>{stringValue(dish.name) ?? "未命名"}</strong>
          <span>{confirmationLabel(stringValue(dish.source))}</span>
        </div>
      ))}
    </article>
  );
}

function EvidenceCard({ artifact }: { artifact: TraceAgentArtifact | null }) {
  const payload = artifactPayload(artifact);
  const dishes = objectArray(payload.dishes);
  return (
    <article className="dish-trace-card">
      <header><span>3</span><div><h3>营养证据检索</h3><small>{artifact?.artifact_id ?? "未运行"}</small></div></header>
      {!artifact ? <p>还没有进入数据库和 RAG 检索。</p> : <>
        <p>知识库：{stringValue(payload.corpus_status) ?? "未知"}</p>
        {dishes.map((dish) => {
          const match = asObject(dish.entity_match);
          return <div className="dish-trace-item" key={stringValue(dish.dish_ref) ?? JSON.stringify(dish)}>
            <strong>{stringValue(match.canonical_name) ?? stringValue(match.query_name) ?? "未知菜品"}</strong>
            <span>实体 {stringValue(match.status) ?? "未知"} · 规则 {objectArray(dish.rules).length} · 引用 {objectArray(dish.citations).length}</span>
            {stringArray(dish.missing_information).length > 0
              && <small>{stringArray(dish.missing_information).join("；")}</small>}
          </div>;
        })}
      </>}
    </article>
  );
}

function GuidanceCard({ artifact }: { artifact: TraceAgentArtifact | null }) {
  const payload = artifactPayload(artifact);
  const dishes = objectArray(payload.dishes);
  return (
    <article className="dish-trace-card">
      <header><span>4</span><div><h3>饮食适宜性建议</h3><small>{artifact?.artifact_id ?? "未运行"}</small></div></header>
      {dishes.length === 0 ? <p>尚未生成逐菜建议。</p> : dishes.map((dish) => (
        <div className="dish-trace-item" key={stringValue(dish.dish_ref) ?? JSON.stringify(dish)}>
          <strong>{stringValue(dish.canonical_name) ?? "未知菜品"}</strong>
          <span>{suitabilityLabel(stringValue(dish.suitability))}</span>
          <small>依据 {numberValue(dish.reason_count) ?? 0} 条 · 动作 {numberValue(dish.action_count) ?? 0} 条</small>
        </div>
      ))}
    </article>
  );
}

function CorrectionForm({
  artifact,
  corrections,
  userId,
  traceId,
}: {
  artifact: TraceAgentArtifact;
  corrections: TraceAgentArtifact[];
  userId: string;
  traceId: string;
}) {
  const queryClient = useQueryClient();
  const dishes = objectArray(artifactPayload(artifact).dishes);
  const [names, setNames] = useState<Record<string, string>>(() => Object.fromEntries(
    dishes.map((dish) => {
      const ref = stringValue(dish.dish_ref) ?? "";
      const candidate = objectArray(dish.candidates)[0];
      return [ref, stringValue(candidate?.label) ?? ""];
    }),
  ));
  const [comment, setComment] = useState("");
  const mutation = useMutation({
    mutationFn: () => api.appendDishRecognitionCorrection(
      userId,
      traceId,
      artifact.artifact_id,
      {
        corrected_dishes: dishes.map((dish) => ({
          dish_ref: stringValue(dish.dish_ref) ?? "",
          corrected_name: names[stringValue(dish.dish_ref) ?? ""]?.trim() ?? "",
        })),
        comment: comment.trim(),
      },
    ),
    onSuccess: async () => {
      setComment("");
      await queryClient.invalidateQueries({ queryKey: ["trace", userId, traceId] });
    },
  });
  const ready = dishes.length > 0
    && dishes.every((dish) => Boolean(names[stringValue(dish.dish_ref) ?? ""]?.trim()))
    && comment.trim().length >= 3;
  return (
    <form className="dish-correction-form" onSubmit={(event) => {
      event.preventDefault();
      if (ready) mutation.mutate();
    }}>
      <header>
        <div><h3>人工更正菜名</h3><p>用于后续识别评测素材；不会静默改写本次识别、菜品库或线上 Prompt。</p></div>
        <span>已追加 {corrections.length} 次</span>
      </header>
      <div className="dish-correction-fields">
        {dishes.map((dish) => {
          const ref = stringValue(dish.dish_ref) ?? "";
          return <label key={ref}>{ref}
            <input value={names[ref] ?? ""} maxLength={128} onChange={(event) => setNames({
              ...names,
              [ref]: event.target.value,
            })} />
          </label>;
        })}
      </div>
      <label>更正说明
        <textarea value={comment} maxLength={2000} onChange={(event) => setComment(event.target.value)} placeholder="例如：这道菜实际是地三鲜，不是红烧茄子。" />
      </label>
      <button disabled={!ready || mutation.isPending} type="submit">{mutation.isPending ? "保存中…" : "追加更正记录"}</button>
      {mutation.isError && <p className="style-ab-form-error">{mutation.error instanceof Error ? mutation.error.message : "保存失败"}</p>}
      {mutation.isSuccess && <p className="style-ab-form-success">更正已追加，原 Artifact 保持不变。</p>}
    </form>
  );
}

function latestArtifact(artifacts: TraceAgentArtifact[], type: string): TraceAgentArtifact | null {
  return [...artifacts].reverse().find((artifact) => normalizeType(artifact.artifact_type) === type) ?? null;
}

function normalizeType(value: string): string {
  return value.toLowerCase().replaceAll(/[^a-z0-9]/g, "");
}

function artifactPayload(artifact: TraceAgentArtifact | null): JsonObject {
  return asObject(artifact?.payload);
}

function asObject(value: unknown): JsonObject {
  return value && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : {};
}

function objectArray(value: unknown): JsonObject[] {
  return Array.isArray(value) ? value.map(asObject).filter((item) => Object.keys(item).length > 0) : [];
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function confirmationLabel(value: string | null): string {
  return ({
    user_confirmed: "用户已确认",
    user_text: "用户文字提供",
    high_confidence_visual: "视觉高置信采用",
  } as Record<string, string>)[value ?? ""] ?? value ?? "来源未知";
}

function suitabilityLabel(value: string | null): string {
  return ({
    suitable: "可以吃",
    suitable_with_adjustment: "调整后可以吃",
    limit: "建议少吃",
    avoid: "按明确限制建议避免",
    insufficient_information: "信息不足",
  } as Record<string, string>)[value ?? ""] ?? value ?? "尚无结论";
}
