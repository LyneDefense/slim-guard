import assert from "node:assert/strict";
import { after, test } from "node:test";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

const server = await createServer({
  root: fileURLToPath(new URL("../", import.meta.url)),
  appType: "custom",
  logLevel: "error",
  optimizeDeps: { noDiscovery: true, entries: [] },
  server: { middlewareMode: true, hmr: false, ws: false, watch: null },
});
after(() => server.close());
const { StyleProfilePage } = await server.ssrLoadModule(
  "/src/components/style/StyleProfilePage.tsx",
);
const { stylesApi } = await server.ssrLoadModule(
  "/src/components/style/api.ts",
);
const style = {
  id: "doctor",
  name: "医生风格",
  description: "自然简短",
  is_default: true,
  active_version_id: "builtin",
  example_count: 21,
  active_version: "基础版本",
};
const version = {
  role_policy_current: true,
  id: "test-v1",
  name: "测试风格 v1",
  status: "ready_for_review",
  build_run_id: "run-1",
  package: {
    version_id: "test-v1",
    guide: { summary: "简洁", rules: [], prohibited_phrases: [] },
    examples: [],
  },
  report: { conclusion: "无明确提升", release_eligible: true },
  review_summary: {
    total: 1,
    pending: 1,
    accept: 0,
    reject: 0,
    auto_failed: 0,
  },
};
const baseRun = {
  id: "run-1",
  name: "本次训练",
  status: "running",
  stage: "construction",
  material_count: 21,
  library_count: 20,
  human_feedback_count: 1,
  unused_count: 2,
  analyzed_count: 21,
  workflow_version: "style-preview-v4-bound-evidence",
  can_resume: true,
  preview_count: 8,
  baseline_version: "builtin",
  last_event_sequence: 1,
  report: {},
  activity: {
    state: "waiting_model",
    message: "等待模型返回",
    purpose: "GuideDraft",
    request_started_at: new Date().toISOString(),
    timeout_seconds: 120,
  },
  usage: { calls: 3, tokens: 900, seconds: 12 },
  heartbeat_at: new Date().toISOString(),
  progress_at: new Date().toISOString(),
  artifacts: ["input_materials", "materials"],
  error: null,
};
function render(path, runOverrides = {}, versionOverrides = {}) {
  const run = { ...baseRun, ...runOverrides };
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  client.setQueryData(["expression-styles"], { items: [style] });
  client.setQueryData(["expression-versions", "doctor"], { items: [{ ...version, ...versionOverrides }] });
  client.setQueryData(["expression-builds", "doctor", 0], {
    items: [run],
    total: 1,
  });
  client.setQueryData(["build-detail", "doctor", "run-1"], run);
  client.setQueryData(["build-events", "doctor", "run-1", 0, 1], {
    items: [],
    total: 0,
    next_sequence: 0,
  });
  client.setQueryData(
    [
      "build-artifact",
      "doctor",
      "run-1",
      "input_materials",
      0,
      run.progress_at,
    ],
    {
      items: [
        {
          user_input: "谢谢",
          original_response: "不用客气",
          desired_response: "不客气",
          correction_opinion: "减少套话",
        },
      ],
      total: 1,
    },
  );
  client.setQueryData(["expression-cases", "doctor", "test-v1", 0], {
    items: [
      {
        id: "case",
        user_input: "怎么称呼你",
        original_response: "我是 SlimGuard。",
        doctor_response: "叫我 SlimGuard 就行。",
        baseline_response: "我是 SlimGuard。",
        test_case: { context: ["这是独立身份问答测试"] },
        automated: { passed: true },
        review: null,
      },
    ],
    total: 1,
    counts: { pending: 1, accept: 0, reject: 0 },
  });
  client.setQueryData(
    [
      "expression-examples",
      "doctor",
      { q: "", role: "", participation: "unused", offset: "0", limit: "20" },
    ],
    {
      items: [
        {
          id: "example",
          user_input: "<script>TEST</script>",
          original_response: "历史回答",
          desired_response: "期望回答",
          revision: 2,
          processed_revision: 1,
          correction_opinion: "少说套话",
          last_result: {
            role: "negative",
            reason: "只有负面意见，不作为正面示例",
          },
          source: "correction",
          actor: "TEST",
          created_at: "2026-09-18T00:00:00Z",
        },
      ],
      total: 21,
      offset: 0,
      limit: 20,
    },
  );
  const html = renderToStaticMarkup(
    createElement(
      QueryClientProvider,
      { client },
      createElement(
        MemoryRouter,
        { initialEntries: [path] },
        createElement(
          Routes,
          null,
          createElement(Route, {
            path: "/styles",
            element: createElement(StyleProfilePage),
          }),
          createElement(Route, {
            path: "/styles/:styleId/:tab",
            element: createElement(StyleProfilePage),
          }),
        ),
      ),
    ),
  );
  client.clear();
  return html;
}
test("construction artifacts display program-owned source locations and escape source text", async () => {
  const { EvidenceCatalogView, ConstructionGuidesView } = await server.ssrLoadModule(
    "/src/components/style/ConstructionArtifacts.tsx",
  );
  const html = renderToStaticMarkup(createElement(EvidenceCatalogView, { items: [{
    ref: "e2", material_ref: "m1", example_id: "sample-a", revision: 3,
    field: "desired_response", start: 0, end: 4, quote: "<script>untrusted</script>",
  }] }));
  for (const text of ["e2", "期望回答", "sample-a", "修订 3", "[0, 4)"])
    assert.ok(html.includes(text));
  assert.ok(!html.includes("<script>"));
  assert.ok(html.includes("&lt;script&gt;"));
  const guide = renderToStaticMarkup(createElement(ConstructionGuidesView, { items: [{
    phase: "merge", level: 1, batch: 2,
    guide: { summary: "验证后的指引", rules: [], prohibited_phrases: [] },
  }] }));
  assert.ok(guide.includes("第 1 层合并"));
  assert.ok(guide.includes("验证后的指引"));
});

test("build offers evidence and intermediate validated guide artifacts", () => {
  const html = render("/styles/doctor/build", {
    artifacts: ["input_materials", "evidence_catalog", "construction_guides"],
  });
  assert.ok(html.includes("可用证据目录（程序绑定）"));
  assert.ok(html.includes("已验证的分批／合并指引"));
});

test("style list precedes detail tabs and uses actual API counts", () => {
  const html = render("/styles");
  assert.ok(html.includes("医生风格"));
  assert.ok(html.includes("21 条示例"));
  assert.ok(html.includes("/styles/doctor/build"));
  assert.ok(!html.includes("追加纠正素材"));
});
test("build page has three nested tabs and publication waits for review", () => {
  const html = render("/styles/doctor/build");
  for (const text of [
    "构建版本",
    "评审版本",
    "追加纠正素材",
    "构建新版本",
    "风格构建过程",
    "等待模型",
    "最后产物更新",
    "查看阶段产物",
    "固定表达示例",
  ])
    assert.ok(html.includes(text));
  assert.match(html, /<button disabled="">采纳并发布<\/button>/);
  for (const text of ["Baseline", "Candidate", "沟通行为", "A/B 人评"])
    assert.ok(!html.includes(text));
});
test("review shows three real texts and three scores without technical hashes", () => {
  const html = render("/styles/doctor/review");
  for (const text of [
    "怎么称呼你",
    "叫我 SlimGuard 就行。",
    "期望医生回答",
    "风格匹配",
    "语义忠实",
    "表达适宜",
    "接受",
    "拒绝",
  ])
    assert.ok(html.includes(text));
  assert.ok(!html.includes("ResponsePlan"));
});

test("fixed preview report has no automatic taste ranking and offers guide edits", async () => {
  const { ReportView } = await server.ssrLoadModule("/src/components/style/TrainingReport.tsx");
  const html = renderToStaticMarkup(createElement(ReportView, { report: {
    schema_version: "2", conclusion: "试答完成，请人工确认语气",
    preview_metrics: { total: 8, content_failed: 0, changed: 6, expression_advisories: 3 },
  } }));
  assert.ok(html.includes("表达建议（仅供参考）"));
  assert.ok(!html.includes("候选更好"));
  const build = render("/styles/doctor/build", {}, { review_summary: {
    total: 8, accept: 8, pending: 0, reject: 0, auto_failed: 0,
  } });
  assert.ok(build.includes("编辑表达指引并重新试答"));
  assert.ok(build.includes("保存为新版本并试答"));
  assert.match(build, /<button>采纳并发布<\/button>/);
  assert.ok(!build.includes("独立测试准备"));
});

test("old workflow failures cannot be endlessly resumed", () => {
  const html = render("/styles/doctor/build", {
    status: "failed", workflow_version: "legacy", can_resume: false, resume_block_reason: "workflow_changed",
    error: "开发题与最终验收题题族重叠",
  });
  assert.ok(!html.includes("恢复本次构建"));
  assert.ok(html.includes("构建流程已更新，请新建构建"));
});

test("model output failures offer batch recovery without blaming user materials", () => {
  const html = render("/styles/doctor/build", {
    status: "failed", can_resume: true, resume_block_reason: null,
    error: "AnalysisBatch 输出校验失败，本次 3 次尝试已用尽",
    artifacts: ["input_materials", "validation:AnalysisBatch:abcdef1234"],
  });
  assert.ok(html.includes("失败批次会携带具体错误重新请求模型"));
  assert.ok(html.includes("已通过的批次不重跑"));
  assert.ok(html.includes("恢复本次构建"));
  assert.ok(!html.includes("请调整素材／指引"));
  assert.ok(!html.includes("旧流程也需"));
  const terminal = render("/styles/doctor/build", {
    status: "failed", can_resume: false, resume_block_reason: "not_retryable", error: "运行时限已耗尽",
  });
  assert.ok(!terminal.includes("构建流程已更新"));
  assert.ok(!terminal.includes("请调整素材／指引"));
});

test("legacy pending versions keep feedback but cannot bypass the coach duty check", () => {
  const html = render("/styles/doctor/build", {}, {
    role_policy_current: false,
    review_summary: { total: 8, accept: 8, pending: 0, reject: 0, auto_failed: 0 },
  });
  assert.match(html, /<button disabled="">采纳并发布<\/button>/);
  assert.ok(html.includes("当前教练职责"));
  const review = render("/styles/doctor/review", {}, { role_policy_current: false });
  assert.ok(review.includes("记录事项及其成功／失败反馈属于系统助手"));
  assert.ok(review.includes("可继续保存反馈，然后重新构建"));
});

test("excluded role sources remain inspectable and escaped in build artifacts", async () => {
  const { BuildArtifact } = await server.ssrLoadModule("/src/components/style/BuildArtifacts.tsx");
  const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity } } });
  client.setQueryData(["build-artifact", "doctor", "run-1", "coach_scope", 0, undefined], {
    items: [{ source_id: "example:old", source_text: "<script>已记录</script>", allowed: false, reason: "操作反馈属于系统助手" }],
    total: 1,
  });
  const html = renderToStaticMarkup(createElement(QueryClientProvider, { client },
    createElement(BuildArtifact, { styleId: "doctor", runId: "run-1", artifactKey: "coach_scope" })));
  assert.ok(html.includes("已排除：非教练职责"));
  assert.ok(html.includes("操作反馈属于系统助手"));
  assert.ok(html.includes("&lt;script&gt;已记录&lt;/script&gt;"));
  assert.ok(!html.includes("<script>"));
  client.clear();
});

test("manual guide revisions send a new build input, not a mutation of a released version", async () => {
  const original = globalThis.fetch;
  let submitted;
  globalThis.fetch = async (url, options) => {
    submitted = { url, options };
    return new Response(JSON.stringify({ id: "new-run" }), { status: 202 });
  };
  const value = { source_version_id: "released", guide: { summary: "直接自然", rules: [], prohibited_phrases: [] } };
  try {
    await stylesApi.build("doctor", "unique-key", value);
    assert.ok(submitted.url.endsWith("/styles/doctor/builds"));
    assert.deepEqual(JSON.parse(submitted.options.body), value);
    assert.equal(submitted.options.headers.get("X-SlimGuard-CSRF"), "1");
    assert.equal(submitted.options.headers.get("Idempotency-Key"), "unique-key");
  } finally { globalThis.fetch = original; }
});
test("corrections contains full searchable paginated library and escapes input", () => {
  const html = render("/styles/doctor/corrections");
  for (const text of [
    "全部素材",
    "未参与构建",
    "已参与构建",
    "正面表达",
    "负面意见",
    "历史回答",
    "期望回答",
    "少说套话",
    "下一页",
  ])
    assert.ok(html.includes(text));
  assert.ok(html.includes("&lt;script&gt;TEST&lt;/script&gt;"));
  assert.ok(!html.includes("<script>"));
});
test("review submits only writable scores, not server actor metadata", async () => {
  const original = globalThis.fetch;
  let submitted;
  globalThis.fetch = async (url, options) => {
    submitted = { url, options };
    return new Response(JSON.stringify({ id: "case" }), { status: 200 });
  };
  try {
    await stylesApi.review("doctor", "case", {
      style_match: 4,
      fidelity: 5,
      appropriateness: 4,
      decision: "accept",
      reason: "",
      desired_response: "",
      actor: "server",
      time: "server",
    });
    const body = JSON.parse(submitted.options.body);
    assert.equal(body.reason, "");
    assert.ok(!("actor" in body));
    assert.equal(submitted.options.headers.get("X-SlimGuard-CSRF"), "1");
  } finally {
    globalThis.fetch = original;
  }
});
test("old style routes and menu pages are not retained", async () => {
  const source = await readFile(
    new URL("../src/App.tsx", import.meta.url),
    "utf8",
  );
  for (const name of [
    "StyleABReviewPage",
    "StyleIterationPage",
    "StyleFeedbackPage",
    "style-ab",
    "style-feedback",
    "style-iterations",
  ])
    assert.ok(!source.includes(name));
  const review = await readFile(
    new URL("../src/components/style/ReviewPanel.tsx", import.meta.url),
    "utf8",
  );
  assert.ok(review.includes('role="dialog"'));
  assert.ok(review.includes("!review.reason.trim()"));
});

test("report shows actual comparison, regressions, dimensions and feedback without claiming success", async () => {
  const { ReportView } = await server.ssrLoadModule(
    "/src/components/style/TrainingReport.tsx",
  );
  const metrics = {
    total: 12,
    candidate_better: 3,
    baseline_better: 2,
    tie: 6,
    uncertain: 1,
    candidate_failed: 1,
    regressions: 1,
    repairs: 2,
    fallbacks: 1,
    dimensions: { baseline: {}, candidate: {} },
  };
  const html = renderToStaticMarkup(
    createElement(ReportView, {
      report: {
        conclusion: "不建议采用",
        metrics,
        regression: { ...metrics, total: 2 },
        difference: {
          rules_added: [],
          rules_removed: [],
          rules_changed: [],
          examples_before: ["a"],
          examples_after: ["b"],
          prompt_changed: false,
        },
        feedback_outcomes: [
          {
            example_id: "review:1",
            role: "negative",
            reason: "未作为正面表达对",
            used_for_rule: true,
          },
        ],
      },
    }),
  );
  for (const text of [
    "不建议采用",
    "新增关键退步",
    "原稿兜底",
    "自然程度",
    "历史拒绝用例回归",
    "固定 Prompt 无变化",
    "已用于 Guide",
  ])
    assert.ok(html.includes(text), text);
});

test("test preparation displays both isolated suites and minimal context", async () => {
  const { TestSuiteView } = await server.ssrLoadModule(
    "/src/components/style/TestSuiteView.tsx",
  );
  const item = {
    id: "1",
    user_input: "名字是什么",
    source_text: "我是助手",
    family: "身份",
    context: ["无额外用户档案"],
  };
  const html = renderToStaticMarkup(
    createElement(TestSuiteView, {
      suite: { development: [item], acceptance: [{ ...item, id: "2" }] },
    }),
  );
  for (const text of [
    "开发评测题",
    "独立验收题",
    "我是助手",
    "无额外用户档案",
    "不会用于本轮优化",
  ])
    assert.ok(html.includes(text), text);
});

test("build progress links to persisted format errors without displaying raw output", () => {
  const html = render("/styles/doctor/build", {
    status: "failed",
    error: "Comparison 结构校验失败，本次 3 次尝试已用尽",
    artifacts: ["input_materials", "validation:Comparison:abcdef1234"],
  });
  for (const text of [
    "查看输出校验详情",
    "输出校验详情：Comparison",
    "恢复本次构建",
    "3 次尝试已用尽",
  ])
    assert.ok(html.includes(text), text);
});

test("diagnostic artifact renders field errors and escapes raw model output for both states", async () => {
  const { BuildArtifact } = await server.ssrLoadModule(
    "/src/components/style/BuildArtifacts.tsx",
  );
  for (const status of ["unresolved", "repaired"]) {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity } },
    });
    const key = "validation:Comparison:abcdef";
    client.setQueryData(["build-artifact", "doctor", "run-1", key, 0, undefined], {
      value: {
        schema_name: "Comparison",
        contract: { version: "test-v1", references: { m1: "review:source-a", m2: "<script>source-b</script>" } },
        status,
        max_attempts_per_execution: 3,
        failures: [
          {
            number: 1,
            execution_attempt: 1,
            time: "2026-09-18T00:00:00Z",
            raw_output: "<script>alert('model')</script>",
            tool_calls: [],
            finish_reason: "stop",
            errors: [
              {
                path: "left_scores.fidelity",
                type: "less_than_equal",
                message: "必须小于等于 5",
              },
            ],
          },
        ],
      },
    });
    const html = renderToStaticMarkup(
      createElement(
        QueryClientProvider,
        { client },
        createElement(BuildArtifact, {
          styleId: "doctor",
          runId: "run-1",
          artifactKey: key,
        }),
      ),
    );
    for (const text of [
      "left_scores.fidelity",
      "less_than_equal",
      "必须小于等于 5",
      "查看模型原始输出",
      "查看本批短编号与原始来源映射",
      "review:source-a",
      "&lt;script&gt;",
    ])
      assert.ok(html.includes(text), text);
    assert.ok(
      html.includes(status === "repaired" ? "已修复，失败记录仍保留" : "尚未修复"),
    );
    assert.ok(!html.includes("<script>"));
    client.clear();
  }
});
