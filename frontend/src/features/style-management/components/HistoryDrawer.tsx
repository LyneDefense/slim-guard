import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { styleStudioApi } from "../api";
import { dateLabel, diagnosticText, type PublishedVersion } from "../model";
import { Dialog } from "./Dialog";
export function HistoryDrawer({
  styleId,
  activeId,
  onClose,
  onRestore,
}: {
  styleId: string;
  activeId?: string;
  onClose: () => void;
  onRestore: (version: PublishedVersion) => void;
}) {
  const query = useQuery({
    queryKey: ["style-studio-history", styleId],
    queryFn: () => styleStudioApi.versions(styleId),
  });
  return (
    <Dialog title="历史版本" drawer onClose={onClose}>
      <div className="studio-dialog-body">
        <p className="studio-muted">
          查看发布时的设定和示例。加载为草稿后可先试聊，再发布并启用。
        </p>
        {query.isLoading && <p role="status">正在读取版本…</p>}
        {query.error && (
          <div className="alert" role="alert">
            {query.error.message}
            <button onClick={() => void query.refetch()}>重试</button>
          </div>
        )}
        {query.data?.items.length === 0 && <p>还没有历史版本。</p>}
        {query.data?.items.map((version) => (
          <article className="studio-version" key={version.id}>
            <header>
              <strong>{version.name}</strong>
              <span
                className={`status ${version.id === activeId ? "status-good" : ""}`}
              >
                {version.id === activeId
                  ? "当前启用"
                  : version.status === "published"
                    ? "已发布"
                    : "历史记录"}
              </span>
            </header>
            <p className="studio-muted">
              {dateLabel(version.created_at)} · {version.actor || "系统"}
              {version.legacy ? " · 旧版配置" : ""}
            </p>
            {version.content && (
              <details>
                <summary>
                  查看配置与 {version.content.examples.length} 组示例
                </summary>
                <h3>表达方式</h3>
                <p className="studio-prewrap">
                  {version.content.expression || "未填写"}
                </p>
                <h3>避免的表达</h3>
                <p className="studio-prewrap">
                  {version.content.avoid || "未填写"}
                </p>
                {version.content.examples.map((example, i) => (
                  <div className="studio-history-example" key={example.id}>
                    <strong>{example.title || `示例 ${i + 1}`}</strong>
                    {example.messages.map((message, j) => (
                      <p key={j}>
                        <span className="studio-muted">
                          {message.role === "user" ? "用户" : "医生"}：
                        </span>
                        {message.content}
                      </p>
                    ))}
                  </div>
                ))}
              </details>
            )}
            {!!version.diagnostics &&
              Object.keys(version.diagnostics).length > 0 && (
                <details>
                  <summary>详细诊断（只读）</summary>
                  <pre>{diagnosticText(version.diagnostics)}</pre>
                </details>
              )}
            {version.restore_block_reason && (
              <p className="studio-muted">{version.restore_block_reason}</p>
            )}
            <button
              type="button"
              disabled={version.status !== "published" || !version.content}
              onClick={() => onRestore(version)}
            >
              加载为草稿
            </button>
          </article>
        ))}
        <LegacyExamples styleId={styleId} />
      </div>
    </Dialog>
  );
}
function LegacyExamples({ styleId }: { styleId: string }) {
  const [open, setOpen] = useState(false);
  const query = useQuery({
    queryKey: ["style-studio-legacy-examples", styleId],
    queryFn: () => styleStudioApi.legacyExamples(styleId),
    enabled: open,
  });
  return (
    <details
      className="studio-legacy"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>旧素材（只读）</summary>
      <p className="studio-muted">
        完整的对话已导入草稿；原回答与纠正意见保留在这里供查阅。
      </p>
      {query.isLoading && <p role="status">正在读取旧素材…</p>}
      {query.error && (
        <p role="alert" className="alert">
          {query.error.message}
          <button onClick={() => void query.refetch()}>重试</button>
        </p>
      )}
      {query.data?.items.length === 0 && (
        <p className="studio-muted">没有旧素材。</p>
      )}
      {query.data?.items.map((item) => (
        <article className="studio-version" key={item.id}>
          <header>
            <strong>{item.user_input || "未填写用户消息"}</strong>
            <span className="status">
              {item.imported ? "已导入" : "仅供查阅"}
            </span>
          </header>
          {item.import_block_reason && (
            <p className="studio-muted">{item.import_block_reason}</p>
          )}
          {item.history?.length > 0 && (
            <details>
              <summary>前文对话</summary>
              {item.history.map((message, i) => (
                <p className="studio-prewrap" key={i}>
                  {message.role === "user" ? "用户" : "医生"}：{message.content}
                </p>
              ))}
            </details>
          )}
          <h3>认可的回答</h3>
          <p className="studio-prewrap">{item.desired_response || "未填写"}</p>
          {item.original_response && (
            <>
              <h3>历史原回答</h3>
              <p className="studio-prewrap">{item.original_response}</p>
            </>
          )}
          {item.correction_opinion && (
            <>
              <h3>纠正意见</h3>
              <p className="studio-prewrap">{item.correction_opinion}</p>
            </>
          )}
        </article>
      ))}
    </details>
  );
}
