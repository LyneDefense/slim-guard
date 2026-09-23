import type { ConversationTaskSummary } from "./types";

export function TaskCards({ tasks }: { tasks: ConversationTaskSummary[] }) {
  return tasks.map((task) => (
    <article key={task.task_id}>
      <span className="eyebrow">跨轮待办 · 第 {task.revision} 次状态</span>
      <strong>{task.objective}</strong>
      <small>{task.unresolved.join("；")}</small>
      <small>有效至 {new Date(task.expires_at).toLocaleString("zh-CN")}</small>
    </article>
  ));
}
