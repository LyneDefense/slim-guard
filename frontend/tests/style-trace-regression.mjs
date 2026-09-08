// Synthetic TEST metadata only. No corpus, network calls, or real human ratings.
import assert from "node:assert/strict";
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
const { StyleTracePanel } = await server.ssrLoadModule("/src/components/trace/StyleTracePanel.tsx");

function artifact(id, type, payload) {
  return { artifact_id: id, artifact_type: type, payload, parent_artifact_ids: [] };
}

function render(payload, changes = {}) {
  return renderToStaticMarkup(createElement(StyleTracePanel, { workflow: {
    mode: "on", invocations: [], transitions: [], timeline: [], shadowComparison: null,
    artifacts: [artifact("TEST-resolution", "style_resolution", payload)],
    ...changes,
  } }));
}

test("Style Trace shows frozen version, act, canary source and expression IDs", () => {
  const html = render({
    style_profile_id: "TEST-style", style_profile_version: "TEST-style-v1",
    requested_style_profile_version: "TEST-style-v1", communication_act: "explain",
    style_selection_source: "canary", example_ids: ["TEST-example-1", "TEST-example-2"],
  });
  assert.ok(html.includes("TEST-style-v1"));
  assert.ok(html.includes("内部用户灰度"));
  assert.ok(html.includes("解释"));
  assert.ok(html.includes("TEST-example-1"));
  assert.ok(html.includes("TEST-example-2"));
  assert.ok(!html.includes("风格资产已回退"));
});

test("asset fallback preserves the requested version and shows the actual default version", () => {
  const html = render({
    style_profile_version: "slimguard_default_v1", requested_style_profile_version: "TEST-missing-v1",
    style_selection_source: "fallback", style_fallback_reason: "profile_not_published",
    communication_act: "ask", example_ids: [],
  });
  assert.ok(html.includes("slimguard_default_v1"));
  assert.ok(html.includes("TEST-missing-v1"));
  assert.ok(html.includes("所选版本未发布"));
  assert.ok(html.includes("本次未使用表达示例"));
  assert.ok(html.includes("询问"));
});

test("legacy traces distinguish absent metadata from an explicitly empty example selection", () => {
  const html = render({ style_profile_version: "TEST-legacy-v1" });
  assert.ok(html.includes("旧 Trace 未记录示例 ID"));
  assert.ok(html.includes("未记录"));
  assert.ok(!html.includes("本次未使用表达示例"));
});

test("latest repair resolution supplies the current act and selected IDs", () => {
  const html = render({}, { artifacts: [
    artifact("TEST-initial", "style_resolution", {
      style_profile_version: "TEST-style-v1", communication_act: "explain", example_ids: ["TEST-old-example"],
    }),
    artifact("TEST-repair", "style_resolution", {
      style_profile_version: "TEST-style-v1", communication_act: "ask", example_ids: ["TEST-repair-example"],
    }),
  ] });
  assert.ok(html.includes("TEST-repair-example"));
  assert.ok(!html.includes("TEST-old-example"));
  assert.ok(html.includes("询问"));
});

test("style metadata does not reveal expression bodies, plans or raw conversations", () => {
  const html = render({}, { artifacts: [
    artifact("TEST-resolution", "style_resolution", {
      style_profile_version: "TEST-style-v1", example_ids: ["TEST-example"],
      examples: [{ text: "PRIVATE_TEST_EXAMPLE" }], raw_chat: "PRIVATE_TEST_CHAT",
    }),
    artifact("TEST-plan", "response_plan", {
      communication_act: "explain",
      content_blocks: [{ kind: "fact", text: "PRIVATE_TEST_FACT", source_refs: [] }],
    }),
    artifact("TEST-response", "styled_response", {
      style_profile_version: "TEST-style-v1", text: "PRIVATE_TEST_REPLY", used_block_ids: [],
    }),
  ] });
  assert.ok(!html.includes("PRIVATE_TEST"));
  assert.ok(html.includes("TEST-example"));
});

test("style adoption labels distinguish a shadow candidate from a final output", () => {
  for (const final of [false, true]) {
    const html = render({ style_profile_version: "TEST-style-v1" }, {
      timeline: [{ sequence: 1, operation: "response_adopted", details: { artifact_id: "TEST-output", final } }],
    });
    assert.ok(html.includes(final ? "最终采用" : "Shadow 候选"));
    assert.ok(html.includes(final ? "进入最终输出" : "未发送给用户"));
  }
});
