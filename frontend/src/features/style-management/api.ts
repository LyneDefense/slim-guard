import { request } from "../../api";
import type {
  DialogueMessage,
  Draft,
  LegacyExample,
  PreviewResult,
  Publication,
  PublishedVersion,
  Studio,
  StudioCheck,
  StudioContent,
  StyleSummary,
} from "./model";
const base = (id: string) => `/styles/${encodeURIComponent(id)}/studio`;
const send = <T>(path: string, body: unknown, method = "POST") =>
  request<T>(path, {
    method,
    headers: { "Content-Type": "application/json", "X-SlimGuard-CSRF": "1" },
    body: JSON.stringify(body),
  });
export const styleStudioApi = {
  list: () => request<{ items: StyleSummary[] }>("/styles"),
  create: (name: string, description: string) =>
    send<StyleSummary>("/styles", { name, description }),
  studio: (id: string) => request<Studio>(base(id)),
  save: (id: string, revision: number, content: StudioContent) =>
    send<Draft>(`${base(id)}/draft`, { revision, content }, "PUT"),
  preview: (
    id: string,
    content: StudioContent,
    history: DialogueMessage[],
    user_input: string,
  ) =>
    send<PreviewResult>(`${base(id)}/preview`, {
      content,
      history,
      user_input,
    }),
  organize: (id: string, content: StudioContent) =>
    send<{ expression: string; avoid: string }>(`${base(id)}/organize`, {
      content,
    }),
  check: (id: string, draft: Draft) =>
    send<StudioCheck>(`${base(id)}/check`, {
      revision: draft.revision,
      content_hash: draft.content_hash,
    }),
  publish: (id: string, draft: Draft, check: StudioCheck) =>
    send<Publication>(`${base(id)}/publish`, {
      revision: draft.revision,
      content_hash: draft.content_hash,
      check_id: check.id,
    }),
  versions: (id: string) =>
    request<{ items: PublishedVersion[] }>(`${base(id)}/versions`),
  legacyExamples: (id: string) =>
    request<{ items: LegacyExample[] }>(`${base(id)}/legacy-examples`),
  restore: (id: string, revision: number, version_id: string) =>
    send<Draft>(`${base(id)}/restore`, { revision, version_id }),
};
