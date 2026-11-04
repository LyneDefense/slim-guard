import type { DialogueMessage } from "./api";

export function DialogueView({ messages }: { messages?: DialogueMessage[] }) {
  if (!messages?.length) return null;
  return <div className="style-dialogue">{messages.map((message, index) => (
    <section key={index}>
      <strong>{message.role === "user" ? "用户" : "医生"}</strong>
      <p>{message.content}</p>
    </section>
  ))}</div>;
}

export function HistoryEditor({ value, onChange }: {
  value: DialogueMessage[];
  onChange: (messages: DialogueMessage[]) => void;
}) {
  return <fieldset className="style-history-editor">
    <legend>前置对话（选填）</legend>
    <p>按轮补充这句话之前的对话，用于学习多轮节奏。当前用户输入和期望回答仍填写在下方。</p>
    {Array.from({ length: value.length / 2 }, (_, turn) => (
      <section key={turn} className="style-material">
        <div className="expression-columns">
          {(["user", "assistant"] as const).map((role, offset) => {
            const index = turn * 2 + offset;
            return <label key={role}>
              第 {turn + 1} 轮 · {role === "user" ? "用户" : "医生"}
              <textarea required maxLength={4000} value={value[index].content}
                onChange={event => onChange(value.map((m, i) =>
                  i === index ? { ...m, content: event.target.value } : m))} />
            </label>;
          })}
        </div>
        <button type="button" onClick={() => onChange(value.filter((_, i) => Math.floor(i / 2) !== turn))}>
          删除此轮
        </button>
      </section>
    ))}
    <button type="button" disabled={value.length >= 22} onClick={() => onChange([
      ...value, { role: "user", content: "" }, { role: "assistant", content: "" },
    ])}>添加一轮前置对话</button>
  </fieldset>;
}
