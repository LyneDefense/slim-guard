export interface StyleSummary {
  id: string;
  name: string;
  description: string;
  is_default: boolean;
  active_version_id: string | null;
  active_version: string | null;
  example_count: number;
}
export interface DialogueMessage {
  role: "user" | "assistant";
  content: string;
}
export interface StyleExample {
  id: string;
  title: string;
  messages: DialogueMessage[];
}
export interface StudioContent {
  expression: string;
  avoid: string;
  examples: StyleExample[];
}
export interface Draft {
  revision: number;
  content: StudioContent;
  content_hash: string;
  updated_at: string;
  actor: string;
  source_version_id: string | null;
}
export interface PublishedVersion {
  id: string;
  name: string;
  status: string;
  created_at: string;
  actor: string;
  content: StudioContent | null;
  legacy: boolean;
  active: boolean;
  diagnostics?: Record<string, unknown>;
  restore_block_reason?: string | null;
}
export interface Studio {
  draft: Draft;
  active_version: PublishedVersion | null;
  style?: StyleSummary;
  legacy_example_count?: number;
  legacy_unimported_count?: number;
}
export interface LegacyExample {
  id: string;
  user_input: string;
  original_response: string;
  desired_response: string;
  correction_opinion: string;
  history: DialogueMessage[];
  imported: boolean;
  import_block_reason: string | null;
}
export interface PreviewResult {
  content_hash: string;
  text: string;
  passed: boolean;
  failure_code: string | null;
  checks: unknown[];
  style_advisory?: boolean;
  model_calls?: number;
}
export interface StudioCheck {
  id: string;
  revision: number;
  content_hash: string;
  passed: boolean;
  release_eligible: boolean;
  checks: ConfigurationCheck[];
  warnings: string[];
  signature: string;
  created_at: string;
}
export interface ConfigurationCheck {
  source_id: string;
  role_passed: boolean;
  safety_passed: boolean;
  issues: Array<{
    dimension: "role" | "safety";
    code: string;
    explanation: string;
  }>;
}
export interface Publication {
  version: PublishedVersion;
  active_version: PublishedVersion | null;
  draft: Draft;
  already_published: boolean;
}
export function publicationState(result: Publication, isDefault: boolean) {
  const active = result.active_version;
  const notice =
    active?.id !== result.version.id
      ? `${result.version.name} 的发布已完成；当前启用的是 ${active?.name || "其他版本"}，没有重新切回旧版。`
      : isDefault
        ? `${result.version.name} 已发布并启用，仅后续新回复使用新版本。`
        : `${result.version.name} 已在本风格启用。当前线上默认风格未改变。`;
  return { active, draft: result.draft, notice };
}
export interface ChatMessage extends DialogueMessage {
  preview?: PreviewResult;
}
export const contentKey = (content: StudioContent) => JSON.stringify(content);
export const sameContent = (left: StudioContent, right: StudioContent) =>
  contentKey(left) === contentKey(right);
export function validateExample(example: StyleExample): string | null {
  if (
    example.messages.length < 2 ||
    example.messages.length > 24 ||
    example.messages.length % 2
  )
    return "示例需要 1–12 轮完整对话。";
  if (
    example.messages.some(
      (message, i) =>
        message.role !== (i % 2 ? "assistant" : "user") ||
        !message.content.trim(),
    )
  )
    return "请填写每轮用户消息和医生回答。";
  if (example.messages.some((message) => message.content.length > 4000))
    return "每条示例消息最多 4000 字。";
  return null;
}
export function exampleFromChat(
  messages: ChatMessage[],
  assistantIndex: number,
): StyleExample {
  const selected = messages.slice(0, assistantIndex + 1);
  if (
    selected.at(-1)?.role !== "assistant" ||
    selected.at(-1)?.preview?.passed === false
  )
    throw new Error("只能保存已通过检查的医生回答。");
  if (selected.length > 24)
    throw new Error(
      "这段对话超过示例的 12 轮上限。请新开一段较短的试聊，或直接添加并编辑示例。",
    );
  return {
    id: `example-${crypto.randomUUID()}`,
    title: "",
    messages: selected.map(({ role, content }) => ({ role, content })),
  };
}
export function dateLabel(value: string) {
  // SQLite persists utc_now() without an offset; browser Date would assume local time.
  const utcValue =
    /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/.test(value)
      ? `${value.replace(" ", "T")}Z`
      : value;
  const date = new Date(utcValue);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("zh-CN", {
        timeZone: "Asia/Shanghai",
        hour12: false,
      });
}
export function diagnosticText(value: unknown): string {
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}
