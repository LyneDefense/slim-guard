// Synthetic TEST fixtures only. No production data, model calls, or real human ratings.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { after, test } from "node:test";
import { fileURLToPath } from "node:url";

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

const { StyleABCaseData } = await server.ssrLoadModule(
  "/src/components/style/StyleABReviewPage.tsx",
);

const fixture = {
  case_id: "TEST-db-id",
  case_key: "style-ab-TEST-case-key",
  source_kind: "synthetic_evaluation",
  source_sample_sha256: "a".repeat(64),
  scenario_title: "TEST 合成趋势判断场景",
  scenario_sha256: "9".repeat(64),
  response_plan_sha256: "b".repeat(64),
  communication_act: "explain",
  required_communication_acts: ["explain"],
  baseline: {
    profile_version: "TEST-baseline-v1",
    generation_model: "TEST-model-baseline",
    example_ids: ["TEST-baseline-example"],
    response_sha256: "c".repeat(64),
    response: { text: "TEST 合成 Baseline 表达", style_profile_version: "TEST-baseline-v1" },
  },
  candidate: {
    profile_version: "TEST-candidate-v2",
    generation_model: "TEST-model-candidate",
    example_ids: ["TEST-candidate-example-1", "TEST-candidate-example-2"],
    response_sha256: "d".repeat(64),
    response: { text: "TEST 合成 Candidate 表达", style_profile_version: "TEST-candidate-v2" },
  },
  candidate_bundle_sha256: "e".repeat(64),
  automated_judge: {
    status: "passed",
    model: "TEST-judge-model",
    evaluation_sha256: "f".repeat(64),
  },
  latest_human_review: {
    review_id: "TEST-review-2",
    case_id: "TEST-db-id",
    supersedes_review_id: "TEST-review-1",
    actor: "TEST-admin-new",
    style_match: 5,
    fidelity: 4,
    appropriateness: 5,
    decision: "accept",
    comment: "TEST 最新更正评分",
    created_at: "2026-09-08T08:01:00Z",
  },
  created_at: "2026-09-08T08:00:00Z",
  scenario: {
    title: "TEST 合成趋势判断场景",
    user_situation: "TEST 用户询问现有记录是否足以判断长期趋势。",
    known_context: ["TEST 当前只有一条合成记录。", "TEST 不得补造更多记录。"],
    response_goal: "TEST 解释为什么当前不能下结论。",
  },
  response_plan: {
    communication_act: "explain",
    content_blocks: [
      { block_id: "TEST-block", kind: "fact", text: "TEST 合成业务计划内容" },
    ],
  },
  reviews: [
    {
      review_id: "TEST-review-1",
      case_id: "TEST-db-id",
      supersedes_review_id: null,
      actor: "TEST-admin-old",
      style_match: 3,
      fidelity: 4,
      appropriateness: 3,
      decision: "reject",
      comment: "TEST 首次评分",
      created_at: "2026-09-08T08:00:00Z",
    },
    {
      review_id: "TEST-review-2",
      case_id: "TEST-db-id",
      supersedes_review_id: "TEST-review-1",
      actor: "TEST-admin-new",
      style_match: 5,
      fidelity: 4,
      appropriateness: 5,
      decision: "accept",
      comment: "TEST 最新更正评分",
      created_at: "2026-09-08T08:01:00Z",
    },
  ],
};

function render(changes = {}) {
  return renderToStaticMarkup(createElement(StyleABCaseData, {
    value: { ...fixture, ...changes },
  }));
}

test("A/B review renders one synthetic plan and both versioned expression outputs", () => {
  const html = render();
  for (const expected of [
    "SYNTHETIC CASE",
    "REVIEW SCENARIO · 合成审核语境",
    "用户刚刚发生了什么",
    "系统已确认的上下文",
    "这条回复要完成什么",
    "TEST 合成趋势判断场景",
    "TEST 用户询问现有记录是否足以判断长期趋势。",
    "TEST 当前只有一条合成记录。",
    "TEST 解释为什么当前不能下结论。",
    "同一份合成 ResponsePlan",
    "TEST 合成业务计划内容",
    "TEST 合成 Baseline 表达",
    "TEST 合成 Candidate 表达",
    "TEST-baseline-v1",
    "TEST-candidate-v2",
    "TEST-model-baseline",
    "TEST-model-candidate",
    "TEST-baseline-example",
    "TEST-candidate-example-1",
    "a".repeat(64),
    "e".repeat(64),
  ]) assert.ok(html.includes(expected), expected);
});

test("automated judge is labelled separately from append-only named human history", () => {
  const html = render();
  assert.ok(html.includes("AUTOMATED JUDGE · 独立记录"));
  assert.ok(html.includes("自动结果不是人工评分，也不会冒充人工审批"));
  assert.ok(html.includes("2 条 append-only 人工评分历史"));
  for (const expected of ["TEST-admin-old", "TEST-admin-new", "TEST 首次评分", "TEST 最新更正评分"]) {
    assert.ok(html.includes(expected), expected);
  }
});

test("comparison ignores unexpected raw conversation and context properties", () => {
  const html = render({
    raw_chat: "PRIVATE_TEST_RAW_CHAT",
    original_context: "PRIVATE_TEST_CONTEXT",
    response_plan: {
      ...fixture.response_plan,
      raw_chat: "PRIVATE_TEST_PLAN_CHAT",
      content_blocks: [{
        block_id: "TEST-block",
        kind: "fact",
        text: "TEST 合成业务计划内容",
        original_context: "PRIVATE_TEST_BLOCK_CONTEXT",
      }],
    },
    scenario: {
      ...fixture.scenario,
      raw_chat: "PRIVATE_TEST_SCENARIO_CHAT",
      user_situation: "TEST 用户询问现有记录是否足以判断长期趋势。",
      known_context: ["TEST 当前只有一条合成记录。"],
    },
    baseline: {
      ...fixture.baseline,
      response: { ...fixture.baseline.response, raw_chat: "PRIVATE_TEST_RESPONSE_CHAT" },
    },
  });
  assert.ok(!html.includes("PRIVATE_TEST"));
  assert.ok(html.includes("TEST 合成业务计划内容"));
});

test("review form identity, CSRF and correction binding stay server-owned and case-scoped", async () => {
  const [componentSource, apiSource] = await Promise.all([
    readFile(new URL("../src/components/style/StyleABReviewPage.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/api.ts", import.meta.url), "utf8"),
  ]);
  assert.match(componentSource, /StyleABCaseReview key=\{selectedId\}/);
  assert.match(componentSource, /key=\{`\$\{detail\.data\.case_id\}:\$\{detail\.data\.latest_human_review\?\.review_id/);
  assert.match(componentSource, /const latest = value\.latest_human_review/);
  assert.match(componentSource, /corrects_review_id: value\.latest_human_review\?\.review_id \?\? null/);
  assert.doesNotMatch(componentSource, /name=["']actor["']/);
  assert.match(apiSource, /"X-SlimGuard-CSRF": "1"/);
  assert.match(apiSource, /JSON\.stringify\(input\)/);
});
