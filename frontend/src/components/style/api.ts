import { request } from "../../api";
export interface Style {
  id: string;
  name: string;
  description: string;
  is_default: boolean;
  active_version_id: string | null;
  active_version: string | null;
  example_count: number;
  created_at: string;
}
export interface Example {
  id: string;
  user_input: string;
  original_response: string;
  desired_response: string;
  category: string;
  status: string;
  source: string;
  actor: string;
  reason: string;
  created_at: string;
  included_versions: string[];
}
export interface Guide {
  summary?: string;
  rules?: Array<{ text: string; confidence: string; evidence_ids: string[] }>;
  prohibited_phrases?: string[];
}
export interface Version {
  id: string;
  name: string;
  status: string;
  stage: string;
  guide: Guide;
  snapshot: { examples: Example[] };
  examples: Example[];
  error: string | null;
  events: Array<{ stage: string; message: string; time?: string }>;
  review_summary: {
    total: number;
    accept: number;
    reject: number;
    pending: number;
    auto_failed: number;
  };
}
export interface Review {
  style_match: number;
  fidelity: number;
  appropriateness: number;
  decision: "accept" | "reject";
  reason: string;
  desired_response: string;
}
export interface Case {
  id: string;
  user_input: string;
  original_response: string;
  doctor_response: string;
  desired_response: string;
  automated: { passed: boolean; failure_code?: string };
  review: Review | null;
}
export interface Page<T> {
  items: T[];
  total: number;
  offset: number;
  limit: number;
}
const base = (id: string) => `/styles/${encodeURIComponent(id)}`;
const send = <T>(path: string, body: unknown = {}, method = "POST") =>
  request<T>(path, {
    method,
    headers: { "Content-Type": "application/json", "X-SlimGuard-CSRF": "1" },
    body: JSON.stringify(body),
  });
export const stylesApi = {
  list: () => request<{ items: Style[] }>("/styles"),
  create: (name: string, description: string) =>
    send<Style>("/styles", { name, description }),
  versions: (id: string) =>
    request<{ items: Version[] }>(`${base(id)}/versions`),
  build: (id: string) => send<Version>(`${base(id)}/versions`),
  action: (id: string, version: string, action: string) =>
    send<Version>(
      `${base(id)}/versions/${encodeURIComponent(version)}/${action}`,
    ),
  examples: (id: string, params: Record<string, string>) =>
    request<Page<Example>>(
      `${base(id)}/examples?${new URLSearchParams(params)}`,
    ),
  append: (
    id: string,
    value: {
      user_input: string;
      original_response: string;
      desired_response: string;
    },
  ) => send<Example>(`${base(id)}/examples`, value),
  state: (id: string, example: string, status: string) =>
    send<Example>(
      `${base(id)}/examples/${encodeURIComponent(example)}`,
      { status },
      "PATCH",
    ),
  cases: (id: string, version: string, offset: number) =>
    request<Page<Case> & { counts: Record<string, number> }>(
      `${base(id)}/versions/${encodeURIComponent(version)}/cases?offset=${offset}&limit=20`,
    ),
  review: (id: string, c: string, value: Review) =>
    send<Case>(`${base(id)}/reviews/${encodeURIComponent(c)}`, {
      style_match: value.style_match,
      fidelity: value.fidelity,
      appropriateness: value.appropriateness,
      decision: value.decision,
      reason: value.reason,
      desired_response: value.desired_response,
    }),
};
