// Synthetic TEST fixtures only. SSR regression checks do not call a live API.
import assert from "node:assert/strict";
import { after, test } from "node:test";
import { fileURLToPath } from "node:url";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

const server = await createServer({
  root: fileURLToPath(new URL("../", import.meta.url)),
  appType: "custom",
  logLevel: "error",
  optimizeDeps: { noDiscovery: true, entries: [] },
  server: { middlewareMode: true, hmr: false, ws: false, watch: null },
});
after(() => server.close());

const [metricsModule, filtersModule, reviewerModule, graphModule, modelModule, apiModule, dishModule] =
  await Promise.all([
    server.ssrLoadModule("/src/components/trace/WorkflowMetrics.tsx"),
    server.ssrLoadModule("/src/components/trace/TraceFilters.tsx"),
    server.ssrLoadModule("/src/components/trace/ReviewerTracePanel.tsx"),
    server.ssrLoadModule("/src/components/trace/WorkflowGraph.tsx"),
    server.ssrLoadModule("/src/components/trace/model.ts"),
    server.ssrLoadModule("/src/api.ts"),
    server.ssrLoadModule("/src/components/trace/DishGuidanceTracePanel.tsx"),
  ]);

function metricsHtml(metrics) {
  return renderToStaticMarkup(createElement(metricsModule.WorkflowMetricsData, { metrics }));
}

function workflow(changes = {}) {
  return {
    mode: "on",
    status: "completed",
    summary: null,
    reviewSummary: { verdicts: [{ artifact_id: "TEST-verdict", verdict: "pass", attempt: 1 }] },
    invocations: [],
    artifacts: [],
    transitions: [],
    timeline: [],
    ...changes,
  };
}

function reviewerHtml(value, metrics) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  if (metrics) client.setQueryData(["workflow-review-metrics", 7], metrics);
  try {
    return renderToStaticMarkup(createElement(
      QueryClientProvider,
      { client },
      createElement(reviewerModule.ReviewerTracePanel, { workflow: value }),
    ));
  } finally {
    client.clear();
  }
}

function dishHtml(value) {
  const client = new QueryClient();
  try {
    return renderToStaticMarkup(createElement(
      QueryClientProvider,
      { client },
      createElement(dishModule.DishGuidanceTracePanel, {
        workflow: value,
        userId: "TEST-user",
        traceId: "TEST-trace",
      }),
    ));
  } finally {
    client.clear();
  }
}

function assertCard(html, label, value) {
  assert.ok(html.includes(`<strong>${value}</strong><span>${label}</span>`), `${label}: ${value}`);
}

test("missing metrics render unknown values without fabricated zero percentages", () => {
  const html = metricsHtml({});
  for (const label of ["p50 延迟", "p95 延迟", "Token 总量", "知识 Claim 引用覆盖率", "无效引用率"]) {
    assertCard(html, label, "未记录");
  }
  assert.ok(html.includes("没有可用的节点失败率记录"));
  assert.ok(!html.includes("0.0%"));
});

test("recorded metrics retain durations, tokens, percentages and sample counts", () => {
  const html = metricsHtml({
    latency_ms: { p50: 1200, p95: 2500, sample_count: 4 },
    tokens: { total: 1000, p50: 200, p95: 400, workflow_count: 4 },
    node_failure_rates: { response_style: { failed: 1, total: 4, rate: 0.25 } },
    citations: {
      coverage_rate: 0.75,
      invalid_rate: 0.1,
      knowledge_claim_count: 4,
      covered_claim_count: 3,
      citation_count: 10,
      invalid_citation_count: 1,
    },
  });
  assertCard(html, "p50 延迟", "1.20 s");
  assertCard(html, "p95 延迟", "2.50 s");
  assertCard(html, "Token 总量", "1,000");
  assertCard(html, "p50 Token", "200");
  assertCard(html, "p95 Token", "400");
  assertCard(html, "知识 Claim 引用覆盖率", "75.0%");
  assertCard(html, "无效引用率", "10.0%");
  assert.match(html, /表达风格 Agent<\/th><td>1<\/td><td>4<\/td><td>25\.0%/);
  assert.ok(html.includes("延迟样本：4 条；Token 工作流样本：4 条"));
});

test("zero denominators suppress rates and latency even when the API supplies zero", () => {
  const html = metricsHtml({
    latency_ms: { sample_count: 0, p50: 0, p95: 0 },
    node_failure_rates: { response_reviewer: { failed: 0, total: 0, rate: 0 } },
    citations: { knowledge_claim_count: 0, citation_count: 0, coverage_rate: 0, invalid_rate: 0 },
  });
  assertCard(html, "p50 延迟", "未记录");
  assertCard(html, "p95 延迟", "未记录");
  assertCard(html, "知识 Claim 引用覆盖率", "未记录");
  assertCard(html, "无效引用率", "未记录");
  assert.ok(!html.includes("0.0%"));
});

test("URL filters retain false values and exclude pagination and unknown fields", () => {
  const params = new URLSearchParams({
    mode: "canary", agent_failure: "false", rag: "true", repair: "false", degraded: "false",
    graph_version: " graph/v2 ", agent_version: "agent & v3", profile_version: "profile-v4",
    offset: "30", unrelated: "TEST-discard",
  });
  const filters = filtersModule.traceFiltersFromParams(params);
  assert.deepEqual(filters, {
    mode: "canary", agent_failure: "false", rag: "true", repair: "false", degraded: "false",
    graph_version: "graph/v2", agent_version: "agent & v3", profile_version: "profile-v4",
  });
  const html = renderToStaticMarkup(createElement(filtersModule.TraceFilters, {
    params,
    onApply: () => {},
  }));
  assert.match(html, /value="canary" selected/);
  assert.match(html, /name="agent_failure"[^]*?value="false" selected/);
  assert.ok(html.includes('name="profile_version"'));
});

test("mode comparisons distinguish execution success from human quality and delivery", () => {
  const html = metricsHtml({ outcomes_by_mode: {
    off: { total: 4, succeeded: 3, degraded: 1, failed: 0 },
    canary: { total: 2, succeeded: 1, degraded: 0, failed: 1 },
  } });
  assert.match(html, /Legacy \/ Off<\/th><td>4<\/td><td>3/);
  assert.match(html, /canary<\/th><td>2<\/td><td>1/);
  assert.ok(html.includes("不代表人工质量评分或渠道送达"));
  assert.ok(metricsHtml({}).includes("没有可用的模式对比记录"));
});

test("trace requests encode identifiers and version values without dropping false filters", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url: new URL(url, "https://test.invalid"), options });
    return new Response(JSON.stringify({ items: [], total: 0, limit: 30, offset: 30 }));
  };
  try {
    await apiModule.api.traces("TEST/user id", 30, {
      mode: "canary", agent_failure: "false", rag: "true", repair: "false", degraded: "false",
      graph_version: "graph/v2", agent_version: "agent & v3+版本", profile_version: "",
    });
    assert.equal(requests.length, 1);
    const { url, options } = requests[0];
    assert.equal(url.pathname, "/api/admin/users/TEST%2Fuser%20id/traces");
    assert.equal(url.searchParams.get("agent_version"), "agent & v3+版本");
    assert.equal(url.searchParams.get("graph_version"), "graph/v2");
    for (const key of ["agent_failure", "repair", "degraded"]) {
      assert.equal(url.searchParams.get(key), "false");
    }
    assert.equal(url.searchParams.get("offset"), "30");
    assert.equal(url.searchParams.get("limit"), "30");
    assert.equal(url.searchParams.has("profile_version"), false);
    assert.equal(options.credentials, "same-origin");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("dish trace shows all four stages and exposes append-only correction", () => {
  const html = dishHtml(workflow({ artifacts: [
    {
      artifact_id: "TEST-recognition", artifact_type: "dish_recognition",
      parent_artifact_ids: [], payload: {
        image_kind: "meal", overall_requires_confirmation: true,
        dishes: [{
          dish_ref: "dish-1", requires_confirmation: true,
          candidates: [{ label: "红烧茄子", confidence: 0.68 }, { label: "地三鲜", confidence: 0.63 }],
          uncertainty_reasons: ["TEST 外观相似"],
        }],
      },
    },
    {
      artifact_id: "TEST-confirmed", artifact_type: "confirmed_dish_set",
      parent_artifact_ids: ["TEST-recognition"],
      payload: { dishes: [{ dish_ref: "dish-1", name: "地三鲜", source: "user_confirmed" }] },
    },
    {
      artifact_id: "TEST-evidence", artifact_type: "dish_evidence_bundle",
      parent_artifact_ids: ["TEST-confirmed"], payload: {
        corpus_status: "available", dishes: [{ dish_ref: "dish-1", entity_match: {
          status: "exact", query_name: "地三鲜", canonical_name: "地三鲜",
        }, rules: [{ rule_id: "TEST-rule" }], citations: [{ citation_id: "TEST-citation" }] }],
      },
    },
    {
      artifact_id: "TEST-guidance", artifact_type: "diet_guidance_assessment",
      parent_artifact_ids: ["TEST-evidence"], payload: { dishes: [{
        dish_ref: "dish-1", canonical_name: "地三鲜", suitability: "limit",
        reason_count: 1, action_count: 1,
      }] },
    },
  ] }));
  for (const expected of [
    "菜品识别与饮食建议", "红烧茄子 68%", "用户已确认", "知识库：available",
    "建议少吃", "人工更正菜名", "追加更正记录",
  ]) assert.ok(html.includes(expected), expected);
  assert.match(html, /value="红烧茄子"/);
  assert.ok(html.includes("不会静默改写本次识别"));
});

test("dish correction request is trace-scoped and CSRF protected", async () => {
  const originalFetch = globalThis.fetch;
  let seen;
  globalThis.fetch = async (url, options) => {
    seen = { url: new URL(url, "https://test.invalid"), options };
    return new Response(JSON.stringify({ artifact_id: "TEST-correction" }), { status: 201 });
  };
  try {
    await apiModule.api.appendDishRecognitionCorrection(
      "TEST/user", "TEST/trace", "TEST/artifact",
      { corrected_dishes: [{ dish_ref: "dish-1", corrected_name: "地三鲜" }], comment: "TEST 更正" },
    );
    assert.equal(seen.url.pathname, "/api/admin/users/TEST%2Fuser/traces/TEST%2Ftrace/dish-recognition-corrections/TEST%2Fartifact");
    assert.equal(seen.options.method, "POST");
    assert.equal(new Headers(seen.options.headers).get("X-SlimGuard-CSRF"), "1");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("Reviewer adopts only explicit final=true events", () => {
  for (const final of [false, undefined]) {
    const html = reviewerHtml(workflow({
      timeline: [{ operation: "response_adopted", details: { artifact_id: "TEST-NONFINAL", final } }],
    }));
    assert.ok(!html.includes("TEST-NONFINAL"));
    assert.ok(html.includes("未记录最终采用 Artifact"));
  }
  const html = reviewerHtml(workflow({
    timeline: [{ operation: "response_adopted", details: { artifact_id: "TEST-FINAL", final: true } }],
  }));
  assert.ok(html.includes("TEST-FINAL"));
  assert.ok(!html.includes("未记录最终采用 Artifact"));
});

test("Shadow candidates never appear as final adoption even in legacy summary payloads", () => {
  const html = reviewerHtml(workflow({
    mode: "shadow",
    reviewSummary: {
      verdicts: [{ artifact_id: "TEST-verdict", verdict: "pass" }],
      comparison: { final_adopted: { artifact_id: "TEST-SHADOW-CANDIDATE" }, changed: true },
    },
    timeline: [{ operation: "response_adopted", details: { artifact_id: "TEST-SHADOW-CANDIDATE", final: false } }],
  }));
  assert.ok(html.includes("Shadow 候选未作为最终输出采用"));
  assert.ok(!html.includes("TEST-SHADOW-CANDIDATE"));
  assert.ok(!html.includes("Artifact 已变化"));
});

test("Reviewer never renders private reason, explanation, excerpt or response bodies", () => {
  const html = reviewerHtml(workflow({
    reviewSummary: { comparison: { original: { artifact_id: "TEST-original" } } },
    artifacts: [
      {
        artifact_id: "TEST-verdict", artifact_type: "reviewer_verdict", parent_artifact_ids: [],
        payload: {
          verdict: "repair", repair_target: "response_style", reason_summary: "PRIVATE_TEST_REASON",
          reason: "PRIVATE_TEST_REASON_ALIAS", explanation: "PRIVATE_TEST_EXPLANATION",
          issues: [{ type: "style_drift", excerpt: "PRIVATE_TEST_EXCERPT", explanation: "PRIVATE_TEST_ISSUE" }],
        },
      },
      {
        artifact_id: "TEST-original", artifact_type: "styled_response", parent_artifact_ids: [],
        payload: { text: "PRIVATE_TEST_REPLY", PRIVATE_TEST_FIELD_NAME: "PRIVATE_TEST_VALUE", schema_version: "1" },
      },
    ],
  }));
  assert.ok(!html.includes("PRIVATE_TEST"));
  assert.ok(html.includes("正文按隐私策略隐藏"));
  assert.ok(html.includes("风格偏移"));
  assert.ok(html.includes("schema_version"));
});

test("Reviewer aggregate and fallback rates use workflows rather than verdict counts", () => {
  const value = workflow({
    reviewSummary: {
      verdicts: [{ artifact_id: "TEST-v1", verdict: "repair", attempt: 1 }, { artifact_id: "TEST-v2", verdict: "pass", attempt: 2 }],
      repair_attempts: [{ attempt: 1, target: "response_style" }],
      repair_counts: { total: 1 },
      budget: { configured_limits: { max_upstream_repairs: 2, max_style_repairs: 1, max_nutrition_repairs: 1 }, repair_attempts_observed: 1 },
    },
  });
  let html = reviewerHtml(value);
  assertCard(html, "修复率", "100.0%");
  assert.ok(html.includes("1 条已审查工作流"));
  assert.ok(html.includes("1 / 2"));
  html = reviewerHtml(value, {
    window: { days: 7 }, counts: { reviewed_workflow_count: 4 },
    rates: { rejection_rate: 0.25, repair_rate: 0.5, degradation_rate: 0.25 },
  });
  assertCard(html, "审查拒绝率", "25.0%");
  assertCard(html, "修复率", "50.0%");
  assert.ok(html.includes("4 条已审查工作流"));
  html = reviewerHtml(value, {
    counts: { reviewed_workflow_count: 0 },
    rates: { rejection_rate: 0, repair_rate: 0, degradation_rate: 0 },
  });
  assertCard(html, "修复率", "未记录");
});

test("workflow graph marks actual return edges without marking normal rerenders", () => {
  const transitions = [
    { from_node: "review_running", to_node: "style_running", transition_type: "repair", reason_code: "review_repair", attempt: 2 },
    { from_node: "style_running", to_node: "review_running", transition_type: "forward", reason_code: "rendered", attempt: 2 },
  ];
  let html = renderToStaticMarkup(createElement(graphModule.WorkflowGraph, { workflow: workflow({ transitions }) }));
  assert.equal((html.match(/aria-label="返回修复"/g) ?? []).length, 1);
  assert.equal((html.match(/aria-label="继续"/g) ?? []).length, 1);
  const invocation = {
    invocation_id: "TEST-style-1", agent_role: "response_style", attempt: 1,
    started_at: "2026-09-08T00:00:00Z", completed_at: "2026-09-08T00:00:01Z", status: "succeeded",
  };
  const model = modelModule.buildWorkflowTrace({ timeline: [], workflow: {
    transitions, invocations: [invocation, { ...invocation, invocation_id: "TEST-style-2", attempt: 2 }],
  } });
  assert.equal(model.invocations[0].repaired, false);
  assert.equal(model.invocations[1].repaired, true);
  html = renderToStaticMarkup(createElement(graphModule.WorkflowGraph, { workflow: { ...model, transitions: [] } }));
  assert.ok(html.includes("旧 Trace 未记录工作流转换"));
  assert.ok(!html.includes('aria-label="继续"'));
});
