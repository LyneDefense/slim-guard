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
  id: "test-v1",
  name: "测试风格 v1",
  status: "ready_for_review",
  stage: "待人工评审",
  guide: { summary: "简洁", rules: [] },
  snapshot: { examples: [] },
  examples: [{}],
  events: [],
  error: null,
  review_summary: {
    total: 1,
    pending: 1,
    accept: 0,
    reject: 0,
    auto_failed: 0,
  },
};
function render(path) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  client.setQueryData(["expression-styles"], { items: [style] });
  client.setQueryData(["expression-versions", "doctor"], { items: [version] });
  client.setQueryData(["expression-cases", "doctor", "test-v1", 0], {
    items: [
      {
        id: "case",
        user_input: "怎么称呼你",
        original_response: "我是 SlimGuard。",
        doctor_response: "叫我 SlimGuard 就行。",
        desired_response: "叫我 SlimGuard。",
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
      { q: "", category: "", source: "", status: "", offset: "0", limit: "20" },
    ],
    {
      items: [
        {
          id: "example",
          user_input: "<script>TEST</script>",
          original_response: "历史回答",
          desired_response: "期望回答",
          category: "expression",
          status: "pending",
          source: "correction",
          actor: "TEST",
          created_at: "2026-09-18T00:00:00Z",
          included_versions: ["测试风格 v1"],
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
    "查看构建过程",
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
test("corrections contains full searchable paginated library and escapes input", () => {
  const html = render("/styles/doctor/corrections");
  for (const text of [
    "全部示例",
    "纯表达差异",
    "历史回答",
    "期望回答",
    "测试风格 v1",
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
