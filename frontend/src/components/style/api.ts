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
export interface MaterialInput {
  user_input: string;
  original_response: string;
  desired_response: string;
  correction_opinion: string;
}
export interface MaterialResult {
  example_id: string;
  role: string;
  reason: string;
  expression_rule?: string;
  selected_example?: boolean;
  used_for_rule?: boolean;
}
export interface Example extends MaterialInput {
  id: string;
  revision: number;
  processed_revision: number;
  participation: string;
  last_run_id: string | null;
  last_result: Partial<MaterialResult>;
  source: string;
  actor: string;
  created_at: string;
}
export interface GuideRule {
  rule_id: string;
  text: string;
  boundary: string;
  evidence: Array<{ example_id: string; revision: number; quote: string }>;
  counterexample_ids: string[];
}
export interface Guide {
  summary: string;
  rules: GuideRule[];
  prohibited_phrases: string[];
}
export interface StylePackage {
  version_id: string;
  guide: Guide;
  package_hash: string;
  examples: Array<{
    id: string;
    revision: number;
    original_response: string;
    desired_response: string;
  }>;
}
export interface Metrics {
  total: number;
  candidate_better: number;
  baseline_better: number;
  tie: number;
  uncertain: number;
  candidate_failed: number;
  baseline_failed: number;
  regressions: number;
  repairs?: number;
  fallbacks?: number;
  dimensions?: Record<string, Record<string, Record<string, number>>>;
}
export interface PackageDiff {
  rules_added: GuideRule[];
  rules_removed: GuideRule[];
  rules_changed: Array<{ before: GuideRule; after: GuideRule }>;
  examples_before: string[];
  examples_after: string[];
  prompt_changed: boolean;
}
export interface Round {
  round: number;
  retained: boolean;
  explanation: string;
  metrics: Metrics;
  difference: PackageDiff;
}
export interface Report {
  conclusion?: string;
  metrics?: Metrics;
  development?: Metrics;
  regression?: Metrics;
  feedback_outcomes?: MaterialResult[];
  rounds?: Round[];
  difference?: PackageDiff;
  release_eligible?: boolean;
  limitations?: string[];
}
export interface Version {
  id: string;
  name: string;
  status: string;
  build_run_id: string | null;
  package: StylePackage;
  report: Report;
  review_summary: {
    total: number;
    accept: number;
    reject: number;
    pending: number;
    auto_failed: number;
  };
}
export interface BuildEvent {
  sequence: number;
  stage: string;
  message: string;
  time: string;
  state?: string;
  artifact_key?: string;
  request_started_at?: string;
  timeout_seconds?: number;
  purpose?: string;
}
export interface BuildRun {
  id: string;
  name: string;
  status: string;
  stage: string;
  activity: Partial<BuildEvent>;
  material_count: number;
  library_count: number;
  human_feedback_count: number;
  unused_count: number;
  analyzed_count: number;
  round_count: number;
  max_rounds: number;
  baseline_version: string | null;
  report: Report;
  usage: { calls?: number; tokens?: number; seconds?: number };
  heartbeat_at: string | null;
  progress_at: string | null;
  error: string | null;
  last_event_sequence: number;
  artifacts?: string[];
}
export interface Review {
  style_match: number;
  fidelity: number;
  appropriateness: number;
  decision: "accept" | "reject";
  reason: string;
  desired_response: string;
}
export interface TestCase {
  id: string;
  family: string;
  user_input: string;
  source_text: string;
  context: string[];
}
export interface ReviewCheck {
  attempt: number;
  passed: boolean;
  output?: string;
  issues: string[];
  verdict?: string;
  policy_version?: string;
}
export interface RewriteOutcome {
  text?: string;
  passed: boolean;
  fallback?: boolean;
  failure_code?: string | null;
  checks?: ReviewCheck[];
}
export interface Case {
  id: string;
  user_input: string;
  original_response: string;
  doctor_response: string;
  baseline_response: string;
  test_case: TestCase;
  automated: RewriteOutcome;
  review: Review | null;
}
export interface EvaluationRow {
  case: TestCase;
  baseline: RewriteOutcome;
  candidate: RewriteOutcome;
  preference: string;
  reason: string;
}
export interface Page<T> {
  items: T[];
  total: number;
  offset: number;
  limit: number;
}
export interface ArtifactResult {
  items?: unknown[];
  total?: number;
  value?: unknown;
}
const base = (id: string) => `/styles/${encodeURIComponent(id)}`;
const send = <T>(
  path: string,
  body: unknown = {},
  method = "POST",
  extra: Record<string, string> = {},
) =>
  request<T>(path, {
    method,
    headers: {
      "Content-Type": "application/json",
      "X-SlimGuard-CSRF": "1",
      ...extra,
    },
    body: JSON.stringify(body),
  });
const buildPath = (id: string, run: string) =>
  `${base(id)}/builds/${encodeURIComponent(run)}`;
export const stylesApi = {
  list: () => request<{ items: Style[] }>("/styles"),
  create: (name: string, description: string) =>
    send<Style>("/styles", { name, description }),
  versions: (id: string) =>
    request<{ items: Version[] }>(`${base(id)}/versions`),
  build: (id: string, key: string) =>
    send<BuildRun>(`${base(id)}/builds`, {}, "POST", {
      "Idempotency-Key": key,
    }),
  builds: (id: string, offset = 0) =>
    request<Page<BuildRun>>(`${base(id)}/builds?offset=${offset}`),
  buildDetail: (id: string, run: string) =>
    request<BuildRun>(buildPath(id, run)),
  buildEvents: (id: string, run: string, after: number) =>
    request<{ items: BuildEvent[]; total: number; next_sequence: number }>(
      `${buildPath(id, run)}/events?after=${after}`,
    ),
  buildAction: (id: string, run: string, action: string) =>
    send<BuildRun>(`${buildPath(id, run)}/${action}`),
  artifact: (id: string, run: string, key: string, offset: number) =>
    request<ArtifactResult>(
      `${buildPath(id, run)}/artifacts?${new URLSearchParams({ key, offset: String(offset) })}`,
    ),
  action: (id: string, version: string, action: string) =>
    send<Version>(
      `${base(id)}/versions/${encodeURIComponent(version)}/${action}`,
    ),
  examples: (id: string, params: Record<string, string>) =>
    request<Page<Example>>(
      `${base(id)}/examples?${new URLSearchParams(params)}`,
    ),
  append: (id: string, value: MaterialInput) =>
    send<Example>(`${base(id)}/examples`, value),
  edit: (id: string, example: string, value: MaterialInput) =>
    send<Example>(
      `${base(id)}/examples/${encodeURIComponent(example)}`,
      value,
      "PUT",
    ),
  remove: (id: string, example: string) =>
    send<{ id: string }>(
      `${base(id)}/examples/${encodeURIComponent(example)}`,
      {},
      "DELETE",
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
