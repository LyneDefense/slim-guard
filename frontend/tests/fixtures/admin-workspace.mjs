// Synthetic UI acceptance data only; never loaded by the application bundle.
export const user = {
  id: "demo-user",
  nickname: "测试用户",
  user_ref: "USER-001",
  first_seen_at: "2026-11-01T08:00:00Z",
  last_seen_at: "2026-11-03T10:00:00Z",
  trace_count: 12,
  issue_count: 1,
};
export const totals = {
  calls: 12,
  invocations: 5,
  input_tokens: 4200,
  output_tokens: 1200,
  total_tokens: 5400,
  unknown_calls: 1,
  failed_calls: 1,
  running_calls: 0,
};
export const style = {
  id: "doctor",
  name: "医生风格",
  description: "简短自然，点评和聊天，不反馈记录操作状态。",
  is_default: true,
  active_version_id: "published",
  active_version: "医生风格 v3",
  example_count: 31,
};
export const studio = {
  draft: {
    revision: 1,
    content: {
      expression: "简短直接，先回应眼前的事。",
      avoid: "避免机械鼓励。",
      examples: [],
    },
    content_hash: "TEST-studio-hash",
    updated_at: "2026-11-10T01:00:00Z",
    actor: "TEST",
    source_version_id: "published",
  },
  active_version: {
    id: "published",
    name: "医生风格 v3",
    status: "published",
    created_at: "2026-11-10T00:00:00Z",
    actor: "TEST",
    content: {
      expression: "简短直接，先回应眼前的事。",
      avoid: "避免机械鼓励。",
      examples: [],
    },
    legacy: false,
    active: true,
  },
};
export const version = {
  id: "published",
  name: "医生风格 v3",
  role_policy_current: true,
  status: "active",
  build_run_id: null,
  package: {
    version_id: "published",
    guide: {
      summary: "直接说重点，避免客服式表达。",
      rules: [],
      prohibited_phrases: [],
    },
    examples: [],
  },
  report: { conclusion: "人工评审通过", release_eligible: true },
  review_summary: {
    total: 1,
    pending: 1,
    accept: 0,
    reject: 0,
    auto_failed: 0,
  },
};
export const reviewCase = {
  id: "case-1",
  user_input: "今天有点忙，晚饭吃了米饭、鸡蛋和青菜。",
  original_response: "这顿饭包含主食、蛋白质和蔬菜，搭配较为完整。",
  doctor_response: "米饭、鸡蛋、青菜都有，搭配挺完整。",
  baseline_response: "主食、蛋白质和蔬菜都包含了。",
  output_revision: 1,
  test_case: { context: ["用户记录了今天的晚餐"] },
  automated: { passed: true },
  review: null,
};
export const summary = {
  business_status: "succeeded",
  reply_status: "succeeded",
  outcome: "completed",
  repaired: false,
  has_issues: false,
  model_calls: 3,
  tool_calls: 1,
  background_model_calls: 0,
  client_status: "not_reported",
  delivery_status: "accepted",
  coverage: "已采集",
};
export const trace = {
  id: "demo-trace",
  user_id: user.id,
  agent_turn_id: "demo-turn",
  trigger_type: "user_message",
  generation_status: "succeeded",
  delivery_status: "accepted",
  reply_kind: "agent",
  created_at: "2026-11-03T10:00:00Z",
  completed_at: "2026-11-03T10:00:05Z",
  duration_ms: 5000,
};
export const call = {
  ...trace,
  nickname: user.nickname,
  input_text: "这是我今天的晚餐",
  image_count: 0,
  process: summary,
};
export const detail = {
  trace,
  input: {
    messages: [
      { item_id: "input-1", text: "这是我今天的晚餐", redacted: false },
    ],
    images: [],
  },
  output: null,
  style_comparison: null,
  process: {
    schema_version: "1",
    summary,
    sources: [],
    artifacts: [],
    background_jobs: [],
    background_calls: [],
    messages: [
      {
        participant: "coach",
        kind: "text",
        text: "有主食，也有蔬菜和蛋白质，搭配挺完整。",
      },
    ],
    nodes: [
      {
        id: "core",
        title: "Core 编排",
        kind: "invocation",
        parent_id: null,
        started_at: trace.created_at,
        completed_at: trace.completed_at,
        duration_ms: 5000,
        status: "succeeded",
        summary: "核对用户输入与执行结果",
        input: { text: "这是我今天的晚餐" },
        output: { status: "completed" },
        evidence_refs: [],
        issues: [],
        raw: {},
      },
    ],
  },
};
export const nutrition = {
  sources: { approved: 8, pending: 2 },
  jobs: { queued: 1 },
  ready_embeddings: 240,
  pending_embeddings: 12,
  runtime: {
    active_release_version: "nutrition-v2",
    active_release_id: "release-1",
    updated_at: "2026-11-03T10:00:00Z",
    updated_by: "operator",
  },
  capabilities: {
    cos_configured: true,
    worker_enabled: true,
    rag_engine: "Hybrid RAG v2",
    embedding_model: "embedding-test",
    rerank_model: "rerank-test",
  },
};
const page = (items = []) => ({
  items,
  total: items.length,
  limit: 30,
  offset: 0,
});
export function fixtureResponse(url) {
  const parsed = new URL(url, "http://localhost"),
    path = parsed.pathname.replace(/^\/api\/admin/, "");
  if (path === "/session" || path === "/auth/login")
    return { username: "operator", expires_at: 2000000000 };
  if (path === "/auth/logout") return { logged_out: true };
  if (path === "/users") return page([user]);
  if (path === `/users/${user.id}`) return user;
  if (path.endsWith("/memories"))
    return [
      {
        id: "memory-1",
        memory_key: "food.preference",
        kind: "preference",
        status: "active",
        value: { statement: "不太能吃辣，偏好清淡饮食。" },
        valid_from: user.first_seen_at,
        assertion: "user_explicit",
        semantic_index: { status: "completed", attempt_count: 1 },
      },
    ];
  if (path.endsWith("/records"))
    return {
      weights: [
        { id: "weight-1", kilograms: 79, occurred_at: user.last_seen_at },
      ],
      body_fat: [],
      meals: [
        { id: "meal-1", meal_type: "dinner", foods: ["米饭", "蒸蛋", "青菜"] },
      ],
      exercises: [],
    };
  if (path.endsWith("/routines"))
    return { preference: { enabled: true, time: "20:00" }, jobs: [] };
  if (path === "/calls") return page([call]);
  if (path.startsWith("/calls/")) return detail;
  if (path.startsWith("/usage/")) {
    if (path.endsWith("/demo-turn") || path.endsWith("/demo-job"))
      return {
        summary: totals,
        executions: 5,
        agents: [{ ...totals, agent_role: "core", executions: 5 }],
        groups: [],
        items: [
          {
            ...totals,
            id: "model-1",
            agent_role: "core",
            component: "agent_generation",
            model: "test-model",
            status: "succeeded",
            started_at: user.last_seen_at,
            total_tokens: 1200,
          },
        ],
        total: 1,
        coverage: "历史未采集的请求不补算。",
        trace_id: trace.id,
      };
    return page([
      {
        ...totals,
        user_id: user.id,
        id: path === "/usage/jobs" ? "demo-job" : "demo-turn",
        started_at: path === "/usage/users" ? null : user.last_seen_at,
        category: "online",
        status: "completed",
        exchanges: 5,
      },
    ]);
  }
  if (path === "/styles") return { items: [style] };
  if (path === "/styles/doctor/studio") return studio;
  if (path.endsWith("/versions"))
    return {
      items: [
        version,
        {
          ...version,
          id: "candidate",
          name: "医生风格 v4 候选",
          status: "ready_for_review",
        },
      ],
    };
  if (path.endsWith("/cases"))
    return {
      ...page([reviewCase]),
      counts: { pending: 1, accept: 0, reject: 0 },
    };
  if (path.endsWith("/builds") || path.endsWith("/examples")) return page();
  if (path === "/nutrition-knowledge/dashboard") return nutrition;
  if (path === "/nutrition-knowledge/runtime")
    return { runtime: nutrition.runtime };
  if (
    /^\/nutrition-knowledge\/(sources|jobs|releases|evaluation-datasets|evaluation-runs)$/.test(
      path,
    )
  )
    return page();
  return undefined;
}
