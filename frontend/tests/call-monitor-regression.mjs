// Synthetic fixtures; these checks never access production services or user data.
import assert from "node:assert/strict";
import { after, test } from "node:test";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { createServer } from "vite";

const server = await createServer({
  root: fileURLToPath(new URL("../", import.meta.url)),
  appType: "custom",
  logLevel: "error",
  optimizeDeps: { noDiscovery: true, entries: [] },
  server: { middlewareMode: true, hmr: false, ws: false, watch: null },
});
after(() => server.close());
const components = await server.ssrLoadModule(
  "/src/features/traces/components.tsx",
);
const model = await server.ssrLoadModule(
  "/src/features/traces/presentation.ts",
);
const api = await server.ssrLoadModule("/src/features/traces/api.ts");
const page = await server.ssrLoadModule(
  "/src/features/traces/CallDetailPage.tsx",
);

test("only live processing or active background work keeps polling", () => {
  const data = detail();
  data.trace.generation_status = "succeeded";
  data.trace.delivery_status = "accepted";
  assert.equal(model.needsRefresh(data), false);
  data.process.background_jobs = [{ status: "queued" }];
  assert.equal(model.needsRefresh(data), true);
  data.process.background_jobs = [];
  data.trace.generation_status = "running";
  assert.equal(model.needsRefresh(data), true);
});
const render = (component, props) =>
  renderToStaticMarkup(createElement(component, props));
const node = (changes = {}) => ({
  id: "node",
  title: "医生生成",
  kind: "invocation",
  parent_id: null,
  started_at: "2026-11-03T10:00:00Z",
  completed_at: "2026-11-03T10:00:01Z",
  duration_ms: 1000,
  status: "succeeded",
  summary: "直接带风格生成",
  input: null,
  output: null,
  evidence_refs: [],
  issues: [],
  raw: null,
  ...changes,
});
function detail(changes = {}) {
  return {
    trace: {
      id: "trace",
      user_id: "user",
      agent_turn_id: "turn",
      created_at: "2026-11-03T10:00:00Z",
      completed_at: "2026-11-03T10:00:01Z",
      duration_ms: 1000,
    },
    input: {
      messages: [{ item_id: "user-message", text: "你好", redacted: false }],
      images: [],
    },
    style_comparison: null,
    output: null,
    process: {
      summary: {
        business_status: "none",
        reply_status: "succeeded",
        outcome: "completed",
        model_calls: 3,
        tool_calls: 0,
        client_status: "not_reported",
        delivery_status: "accepted",
        coverage: "已记录调用",
      },
      nodes: [node()],
      sources: [],
      artifacts: [],
      messages: [{ participant: "coach", text: "你好，今天怎么样？" }],
      background_jobs: [],
      background_calls: [],
      ...changes,
    },
  };
}

test("detail matches approved information hierarchy and shows unknown client acknowledgement", () => {
  const html = renderToStaticMarkup(
    createElement(
      MemoryRouter,
      { initialEntries: ["/users/user/traces/trace"] },
      createElement(page.CallDetailView, { data: detail() }),
    ),
  );
  for (const label of [
    "单次调用详情",
    "用户输入",
    "业务执行",
    "医生回复",
    "服务／渠道",
    "客户端",
    "未上报",
    "服务耗时",
    "处理过程",
    "输入输出",
    "实际返回给 App／渠道的内容",
  ])
    assert.ok(html.includes(label), label);
  assert.ok(!html.includes("用户已收到"));
});

test("tree groups model requests under tool rather than duplicating tool or request", () => {
  const nodes = [
    node({ id: "core", title: "Core 编排" }),
    node({
      id: "tool",
      title: "inspect_image",
      kind: "tool",
      parent_id: "core",
    }),
    node({ id: "model", title: "模型请求", kind: "model", parent_id: "tool" }),
  ];
  const html = render(components.Flow, {
    nodes,
    selected: "model",
    expanded: true,
    onSelect() {},
  });
  assert.equal(html.match(/inspect_image/g).length, 1);
  assert.equal(html.match(/class="call-children"/g).length, 2);
  assert.ok(html.includes('aria-pressed="true"'));
});

test("collapsed tree hides internal calls without losing main phases", () => {
  const html = render(components.Flow, {
    nodes: [
      node({ id: "core", title: "Core 编排" }),
      node({ id: "request", parent_id: "core", title: "内部模型请求" }),
    ],
    selected: "core",
    expanded: false,
    onSelect() {},
  });
  assert.ok(html.includes("Core 编排"));
  assert.ok(!html.includes("内部模型请求"));
});

test("final output contains only adopted messages, not candidate artifacts", () => {
  const data = detail({
    artifacts: [
      { artifact_id: "rejected", payload: { text: "必须不显示的候选稿" } },
    ],
  });
  const html = render(components.FinalOutput, { data });
  assert.ok(html.includes("你好，今天怎么样？"));
  assert.ok(!html.includes("必须不显示的候选稿"));
});

test("partial output retains a genuine operation notice without fabricating coach text", () => {
  const html = render(components.FinalOutput, {
    data: detail({
      messages: [
        {
          participant: "system_assistant",
          text: "晚餐已记录。医生回复暂时不可用。",
        },
      ],
    }),
  });
  assert.ok(html.includes("晚餐已记录"));
  assert.ok(!html.includes("最终采用稿"));
});

test("legacy unblocked output is labelled as historical instead of guessing speaker", () => {
  const data = detail({ messages: [] });
  data.output = { content: "历史回复" };
  const html = render(components.FinalOutput, { data });
  assert.ok(html.includes("历史／单段输出"));
  assert.ok(!html.includes("医生教练 · 最终采用稿"));
});

test("waterfall skips missing timing instead of inventing zero-length events", () => {
  const html = render(components.Waterfall, {
    nodes: [node({ id: "known" }), node({ id: "unknown", duration_ms: null })],
    start: "2026-11-03T10:00:00Z",
    end: "2026-11-03T10:00:01Z",
    selected: "known",
    onSelect() {},
  });
  assert.equal(html.match(/class="call-time-row"/g).length, 1);
  assert.ok(html.includes("父阶段包含子调用，不能相加"));
});

test("unknown dates and timings have honest labels", () => {
  assert.equal(model.date("invalid"), "未记录");
  assert.equal(model.elapsed(null), "未记录");
  assert.equal(model.elapsed(0), "0ms");
  assert.equal(model.elapsed(1500), "1.5s");
});

test("evidence references deduplicate across structured nested input", () => {
  assert.deepEqual(
    model.evidenceRefs({
      output: { source_refs: ["a", "a"] },
      nested: [{ allowed_source_refs: ["b"] }],
    }),
    ["a", "b"],
  );
});

test("API filters are encoded, scoped, and independent of inspector state", async () => {
  const original = globalThis.fetch;
  let url;
  globalThis.fetch = async (value) => {
    url = value;
    return new Response(JSON.stringify({ items: [] }), { status: 200 });
  };
  try {
    await api.callApi.list(
      new URLSearchParams({
        search: "晚餐&图片",
        step: "secret",
        return: "ignored",
        offset: "30",
      }),
      "user / a",
    );
    const parsed = new URL(url, "https://test.local");
    assert.equal(parsed.pathname, "/api/admin/calls");
    assert.equal(parsed.searchParams.get("user_id"), "user / a");
    assert.equal(parsed.searchParams.get("search"), "晚餐&图片");
    assert.ok(!parsed.searchParams.has("step"));
    await api.callApi.detail("user / a", "trace?x");
    assert.ok(url.includes("user%20%2F%20a/traces/trace%3Fx"));
  } finally {
    globalThis.fetch = original;
  }
});
