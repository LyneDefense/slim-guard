import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, Navigate, useParams } from "react-router-dom";
import { styleStudioApi } from "../api";
import {
  dateLabel,
  exampleFromChat,
  sameContent,
  type Studio,
  type StyleExample,
} from "../model";
import { useStudio } from "../useStudio";
import { ChatPanel } from "../components/ChatPanel";
import { CheckResult } from "../components/CheckResult";
import { ExampleEditor } from "../components/ExampleEditor";
import { OrganizeDialog } from "../components/OrganizeDialog";
import {
  WorkflowDialogs,
  type StudioOverlay,
} from "../components/WorkflowDialogs";
import "../studio.css";
export function LegacyStyleRedirect() {
  const { styleId = "" } = useParams();
  return <Navigate to={`/styles/${encodeURIComponent(styleId)}`} replace />;
}
export function StyleStudioPage() {
  const { styleId = "" } = useParams();
  const query = useQuery({
    queryKey: ["style-studio", styleId],
    queryFn: () => styleStudioApi.studio(styleId),
    retry: false,
  });
  const styles = useQuery({
    queryKey: ["expression-styles"],
    queryFn: styleStudioApi.list,
  });
  if (query.isLoading)
    return (
      <div className="state-card" role="status">
        正在读取角色草稿…
      </div>
    );
  if (query.error)
    return (
      <div className="state-card state-error" role="alert">
        {query.error.message}
        <button onClick={() => void query.refetch()}>重试</button>
        <Link to="/styles">返回表达风格</Link>
      </div>
    );
  if (!query.data) return null;
  const style =
    query.data.style ?? styles.data?.items.find((item) => item.id === styleId);
  return (
    <StudioEditor
      key={styleId}
      styleId={styleId}
      name={style?.name || styleId}
      isDefault={style?.is_default ?? false}
      initial={query.data}
    />
  );
}
export function StudioEditor({
  styleId,
  name,
  initial,
  isDefault,
}: {
  styleId: string;
  name: string;
  initial: Studio;
  isDefault: boolean;
}) {
  const studio = useStudio(styleId, initial, isDefault);
  const [overlay, setOverlay] = useState<StudioOverlay>(null);
  const [example, setExample] = useState<{
    value: StyleExample;
    existing: boolean;
  } | null>(null);
  const [focus, setFocus] = useState(false);
  const [organizing, setOrganizing] = useState(false);
  const [suggestion, setSuggestion] = useState<{
    expression: string;
    avoid: string;
  } | null>(null);
  const locked = !!studio.busy || organizing;
  const aligned =
    studio.active?.content &&
    sameContent(studio.content, studio.active.content);
  const startPublish = async () => {
    const result =
      studio.checkValid && studio.check
        ? studio.check
        : await studio.runCheck();
    if (result?.passed && result.release_eligible)
      setOverlay({ kind: "publish", check: result });
    else if (result) setFocus(false);
  };
  const organize = async () => {
    setOrganizing(true);
    studio.setError("");
    try {
      setSuggestion(await styleStudioApi.organize(styleId, studio.content));
    } catch (reason) {
      studio.setError(
        reason instanceof Error ? reason.message : "整理失败，请重试。",
      );
    } finally {
      setOrganizing(false);
    }
  };
  const addExample = () =>
    setExample({
      value: {
        id: `example-${crypto.randomUUID()}`,
        title: "",
        messages: [
          { role: "user", content: "" },
          { role: "assistant", content: "" },
        ],
      },
      existing: false,
    });
  return (
    <div className={`page style-studio ${focus ? "studio-focus" : ""}`}>
      <Link className="back-link" to="/styles">
        ← 表达风格
      </Link>
      <header className="page-header studio-titlebar">
        <div>
          <h1>{name} · 表达工作台</h1>
          <p>设定怎么说，用实际对话试一试。</p>
        </div>
        <div className="studio-actions">
          <button
            type="button"
            disabled={locked}
            onClick={() => setOverlay("history")}
          >
            历史版本
          </button>
          <button
            type="button"
            disabled={locked || !studio.dirty || studio.conflict}
            onClick={() => void studio.save()}
          >
            {studio.busy === "save" ? "正在保存…" : "保存草稿"}
          </button>
          <button
            type="button"
            className="studio-primary"
            disabled={
              locked || studio.conflict || !studio.content.expression.trim()
            }
            onClick={() => void startPublish()}
          >
            {studio.busy === "check"
              ? "正在检查…"
              : studio.busy === "publish"
                ? "正在发布…"
                : "发布并启用"}
          </button>
        </div>
      </header>
      <div className="studio-statusbar">
        <div>
          <span className={`status ${studio.active ? "status-good" : ""}`}>
            {studio.active
              ? `${isDefault ? "线上启用" : "本风格已启用（非线上默认）"} · ${studio.active.name}`
              : "尚未启用"}
          </span>
          <span
            className={`status ${aligned && !studio.dirty ? "status-good" : "status-warn"}`}
          >
            {studio.dirty
              ? "草稿有未保存修改"
              : aligned
                ? "与启用版本一致"
                : "草稿未发布"}
          </span>
        </div>
        <span className="studio-muted">
          {studio.dirty
            ? "修改仅保留在当前浏览器"
            : `已保存 · ${dateLabel(studio.draft.updated_at)}`}
        </span>
      </div>
      {studio.error && (
        <div className="alert" role="alert">
          {studio.error}
        </div>
      )}
      {studio.conflict && (
        <div className="studio-notice">
          检测到服务器草稿或版本状态变化，本地修改仍保留。
          <button
            type="button"
            disabled={locked}
            onClick={() => setOverlay("reload")}
          >
            载入服务器草稿
          </button>
        </div>
      )}
      <div className="studio-toolbar">
        <span className="studio-muted">
          {isDefault
            ? "保存草稿不会影响线上。发布并启用后，仅后续新回复使用新版本。"
            : "这是非默认风格。保存或发布仅更新本风格，当前线上默认风格不改变。"}
        </span>
        <button
          type="button"
          aria-pressed={focus}
          onClick={() => setFocus(!focus)}
        >
          {focus ? "返回并排编辑" : "聚焦试聊"}
        </button>
      </div>
      <div className="studio-workspace">
        <div className="studio-editor">
          <section className="studio-panel" aria-label="角色设定">
            <header className="studio-panel-heading">
              <h2>角色设定</h2>
              <button
                type="button"
                disabled={locked || !studio.content.expression.trim()}
                onClick={() => void organize()}
              >
                {organizing ? "正在整理…" : "AI 帮我整理"}
              </button>
            </header>
            <fieldset className="studio-fields" disabled={locked}>
              <label className="studio-field">
                表达方式
                <textarea
                  className="studio-expression"
                  value={studio.content.expression}
                  maxLength={1000}
                  placeholder="希望角色怎样说话？可以直接写口吻、节奏和表达习惯。"
                  onChange={(event) =>
                    studio.update({
                      ...studio.content,
                      expression: event.target.value,
                    })
                  }
                />
                <small>{studio.content.expression.length} / 1000</small>
              </label>
              <details>
                <summary>避免的表达（可选）</summary>
                <label className="studio-field">
                  不希望出现的口吻
                  <textarea
                    value={studio.content.avoid}
                    maxLength={1000}
                    onChange={(event) =>
                      studio.update({
                        ...studio.content,
                        avoid: event.target.value,
                      })
                    }
                  />
                </label>
              </details>
            </fieldset>
            <div className="studio-boundary">
              <strong>固定职责</strong>
              <p>
                只点评和对话，不执行记录、不播报操作结果。事实与医疗安全边界不随风格改变。
              </p>
            </div>
          </section>
          <section className="studio-panel" aria-label="对话示例">
            <header className="studio-panel-heading">
              <div>
                <h2>
                  对话示例{" "}
                  <span className="studio-muted">
                    {studio.content.examples.length} 组
                  </span>
                </h2>
                <p>直接写你认可的回答，支持多轮。</p>
              </div>
              <button
                type="button"
                disabled={locked || studio.content.examples.length >= 64}
                onClick={addExample}
              >
                ＋ 添加
              </button>
            </header>
            <div className="studio-examples">
              {studio.content.examples.length === 0 && (
                <p className="studio-muted">
                  还没有示例。可以直接添加，也可以将试聊回答保存为示例。
                </p>
              )}
              {studio.content.examples.map((item, i) => (
                <article className="studio-example" key={item.id}>
                  <header>
                    <strong>
                      {item.title || `示例 ${i + 1}`}{" "}
                      <span className="studio-muted">
                        {item.messages.length / 2} 轮
                      </span>
                    </strong>
                    <button
                      type="button"
                      disabled={locked}
                      aria-label={`编辑${item.title || `示例 ${i + 1}`}`}
                      onClick={() =>
                        setExample({ value: item, existing: true })
                      }
                    >
                      编辑
                    </button>
                  </header>
                  {item.messages.slice(0, 2).map((message, index) => (
                    <p key={index}>
                      <span className="studio-muted">
                        {message.role === "user" ? "用户" : "医生"}
                      </span>
                      <span>{message.content}</span>
                    </p>
                  ))}
                </article>
              ))}
            </div>
            <div className="studio-example-note">
              示例只用于学习表达，不作为当前用户的事实。
            </div>
          </section>
          <section className="studio-panel studio-check-section">
            <header className="studio-panel-heading">
              <div>
                <h2>必要检查</h2>
                <p>发布前检查配置与职责、安全边界，无需批量回放。</p>
              </div>
              <button
                type="button"
                disabled={
                  locked || studio.conflict || !studio.content.expression.trim()
                }
                onClick={() => void studio.runCheck()}
              >
                {studio.busy === "check" ? "正在检查…" : "检查草稿"}
              </button>
            </header>
            {studio.checkValid && studio.check ? (
              <CheckResult
                result={studio.check}
                examples={studio.content.examples}
              />
            ) : (
              <p className="studio-muted studio-panel-note">
                发布时会先保存并检查当前草稿，检查通过后再确认发布。
              </p>
            )}
          </section>
        </div>
        <ChatPanel
          messages={studio.messages}
          pending={studio.previewPending}
          stale={studio.stale}
          error={studio.previewError}
          onSend={studio.send}
          onRestart={studio.restart}
          onAdopt={(index) => {
            if (locked) return;
            if (studio.content.examples.length >= 64) {
              studio.setError("草稿最多保存 64 组示例，请先整理已有示例。");
              return;
            }
            try {
              setExample({
                value: exampleFromChat(studio.messages, index),
                existing: false,
              });
            } catch (reason) {
              studio.setError(
                reason instanceof Error ? reason.message : "无法保存示例。",
              );
            }
          }}
        />
      </div>
      <p className="studio-feedback" role="status" aria-live="polite">
        {studio.notice}
      </p>
      {example && (
        <ExampleEditor
          initial={example.value}
          onClose={() => setExample(null)}
          onDelete={
            example.existing
              ? () => {
                  studio.update({
                    ...studio.content,
                    examples: studio.content.examples.filter(
                      (item) => item.id !== example.value.id,
                    ),
                  });
                  setExample(null);
                  studio.setNotice("已从草稿移除示例，线上版本未改变。");
                }
              : undefined
          }
          onSave={(value) => {
            studio.update({
              ...studio.content,
              examples: example.existing
                ? studio.content.examples.map((item) =>
                    item.id === value.id ? value : item,
                  )
                : [...studio.content.examples, value],
            });
            setExample(null);
            studio.setNotice("示例已加入草稿，请保存草稿。线上版本未改变。");
          }}
        />
      )}
      <OrganizeDialog
        suggestion={suggestion}
        setSuggestion={setSuggestion}
        studio={studio}
      />
      <WorkflowDialogs
        overlay={overlay}
        setOverlay={setOverlay}
        studio={studio}
        styleId={styleId}
        name={name}
        isDefault={isDefault}
        locked={locked}
      />
    </div>
  );
}
