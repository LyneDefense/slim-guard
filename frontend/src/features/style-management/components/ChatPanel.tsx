import { useState } from "react";
import { diagnosticText, type ChatMessage } from "../model";
export function ChatPanel({
  messages,
  pending,
  stale,
  error,
  onSend,
  onRestart,
  onAdopt,
}: {
  messages: ChatMessage[];
  pending: boolean;
  stale: boolean;
  error: string;
  onSend: (value: string) => Promise<boolean>;
  onRestart: () => void;
  onAdopt: (index: number) => void;
}) {
  const [input, setInput] = useState("");
  const send = async (value: string) => {
    if (await onSend(value)) setInput("");
  };
  return (
    <section className="studio-panel studio-preview" aria-label="草稿试聊">
      <header className="studio-panel-heading">
        <div>
          <h2>试聊</h2>
          <span className="status">当前草稿</span>
        </div>
        <button type="button" onClick={onRestart}>
          新对话
        </button>
      </header>
      <div className="studio-preview-meta">
        <span>与线上共用生成及必要安全边界</span>
        <span>不写入真实用户记录</span>
      </div>
      {stale && (
        <div className="studio-notice" role="status">
          草稿已更新，已有对话使用的是旧设定。
          <button type="button" onClick={onRestart}>
            用新草稿重新试聊
          </button>
        </div>
      )}
      <div
        className="studio-chat"
        role="log"
        aria-label="试聊记录"
        aria-live="polite"
        aria-busy={pending}
      >
        {!messages.length && (
          <div className="studio-chat-empty">
            <strong>直接试一段对话</strong>
            <p>
              使用当前编辑内容，即使还没有保存。可以接着追问，观察多轮表现。
            </p>
          </div>
        )}
        {messages.map((message, index) => (
          <article
            className={`studio-message ${message.role === "user" ? "studio-user" : ""}`}
            key={index}
          >
            <span className="studio-message-role">
              {message.role === "user" ? "你" : "医生教练"}
            </span>
            <div className="studio-bubble">{message.content}</div>
            {message.preview && (
              <div className="studio-answer-actions">
                <button
                  type="button"
                  className="studio-quiet"
                  onClick={() => onAdopt(index)}
                >
                  保存为示例
                </button>
                {message.preview.style_advisory && (
                  <span className="studio-muted">仍有表达建议</span>
                )}
                <details>
                  <summary>查看检查</summary>
                  <pre>{diagnosticText(message.preview.checks)}</pre>
                </details>
              </div>
            )}
          </article>
        ))}
        {pending && (
          <p className="studio-muted" role="status">
            正在生成并检查回答…
          </p>
        )}
        {error && (
          <p className="alert" role="alert">
            {error}
          </p>
        )}
      </div>
      <div className="studio-scenes">
        {[
          ["日常聊天", "你好。"],
          ["不想运动", "我今天不想运动。"],
          ["一餐点评", "晚餐吃了米饭、鸡蛋和白菜。"],
        ].map(([label, value]) => (
          <button
            key={label}
            type="button"
            disabled={pending || stale}
            onClick={() => setInput(value)}
          >
            {label}
          </button>
        ))}
      </div>
      <form
        className="studio-compose"
        onSubmit={(event) => {
          event.preventDefault();
          void send(input);
        }}
      >
        <label className="studio-field">
          试聊消息
          <textarea
            value={input}
            maxLength={4000}
            placeholder="输入一句话，看看医生会怎么回应…"
            onChange={(event) => setInput(event.target.value)}
          />
        </label>
        <div>
          <span className="studio-muted">只生成点评和对话，不执行真实操作</span>
          <button
            type="submit"
            className="studio-primary"
            disabled={pending || stale || !input.trim()}
          >
            发送
          </button>
        </div>
      </form>
    </section>
  );
}
