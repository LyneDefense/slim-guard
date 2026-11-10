import { useState } from "react";
import { Dialog } from "./Dialog";
import { validateExample, type StyleExample } from "../model";
export function ExampleEditor({
  initial,
  onSave,
  onDelete,
  onClose,
}: {
  initial: StyleExample;
  onSave: (example: StyleExample) => void;
  onDelete?: () => void;
  onClose: () => void;
}) {
  const [example, setExample] = useState(initial);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState(false);
  const update = (index: number, value: string) =>
    setExample((current) => ({
      ...current,
      messages: current.messages.map((message, i) =>
        i === index ? { ...message, content: value } : message,
      ),
    }));
  return (
    <Dialog
      title={onDelete ? "编辑对话示例" : "添加对话示例"}
      onClose={onClose}
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          const message = validateExample(example);
          if (message) return setError(message);
          onSave(example);
        }}
      >
        <div className="studio-dialog-body">
          <p className="studio-muted">
            填写一段你认可的完整对话。示例只学习表达，不作为当前用户事实。
          </p>
          <label className="studio-field">
            示例名称（可选）
            <input
              value={example.title}
              maxLength={100}
              onChange={(event) =>
                setExample({ ...example, title: event.target.value })
              }
            />
          </label>
          {Array.from({ length: example.messages.length / 2 }, (_, turn) => (
            <fieldset className="studio-turn" key={turn}>
              <legend>第 {turn + 1} 轮</legend>
              <label className="studio-field">
                用户
                <textarea
                  required
                  maxLength={4000}
                  value={example.messages[turn * 2].content}
                  onChange={(event) => update(turn * 2, event.target.value)}
                />
              </label>
              <label className="studio-field">
                医生回答
                <textarea
                  required
                  maxLength={4000}
                  value={example.messages[turn * 2 + 1].content}
                  onChange={(event) => update(turn * 2 + 1, event.target.value)}
                />
              </label>
              {example.messages.length > 2 && (
                <button
                  type="button"
                  className="studio-quiet"
                  onClick={() =>
                    setExample({
                      ...example,
                      messages: example.messages.filter(
                        (_, i) => Math.floor(i / 2) !== turn,
                      ),
                    })
                  }
                >
                  删除这一轮
                </button>
              )}
            </fieldset>
          ))}
          <button
            type="button"
            disabled={example.messages.length >= 24}
            onClick={() =>
              setExample({
                ...example,
                messages: [
                  ...example.messages,
                  { role: "user", content: "" },
                  { role: "assistant", content: "" },
                ],
              })
            }
          >
            ＋ 添加一轮
          </button>
          {error && (
            <p role="alert" className="alert">
              {error}
            </p>
          )}
          {deleting && (
            <div className="studio-notice" role="alert">
              确定从草稿删除这个示例？已发布版本不受影响。
              <button
                type="button"
                className="studio-danger"
                onClick={onDelete}
              >
                确定删除
              </button>
              <button type="button" onClick={() => setDeleting(false)}>
                保留
              </button>
            </div>
          )}
        </div>
        <footer className="studio-dialog-actions">
          {onDelete && (
            <button
              type="button"
              className="studio-danger"
              onClick={() => setDeleting(true)}
            >
              删除示例
            </button>
          )}
          <button type="button" onClick={onClose}>
            取消
          </button>
          <button className="studio-primary" type="submit">
            保存到草稿
          </button>
        </footer>
      </form>
    </Dialog>
  );
}
