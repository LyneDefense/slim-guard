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
    mode: "core_primary", invocations: [], transitions: [], timeline: [],
    artifacts: [artifact("TEST-resolution", "style_resolution", payload)],
    ...changes,
  } }));
}

test("direct coach trace shows the loaded style and grounding, never a missing neutral draft", () => {
  const html = render({}, { invocations: [{ agent_role: "coach_response", status: "succeeded" }],
    artifacts: [
      artifact("TEST-style", "style_resolution", { profile_version: "TEST-published", source: "runtime_active", example_ids: ["TEST-1"] }),
      artifact("TEST-reply", "coach_reply", { output: { text: "你确定？" }, validation_errors: [] }),
      { ...artifact("TEST-review", "coach_grounding_review", { output: { role_passed: true } }), parent_artifact_ids: ["TEST-reply"] },
      artifact("TEST-final", "outgoing_messages", { status: "succeeded" }),
    ] });
  assert.ok(html.includes("医生直接回复"));
  assert.ok(html.includes("TEST-published"));
  assert.ok(html.includes("你确定？"));
  assert.ok(!html.includes("未记录中性稿"));
});

test("direct coach trace exposes exact structural diagnostics and an undelivered doctor response", () => {
  const html = render({}, { invocations: [{ agent_role: "coach_response", status: "failed" }],
    artifacts: [artifact("TEST-broken", "coach_reply", { failure_code: "structured_output_invalid",
      validation_errors: [{ path: ["source_refs"], code: "unavailable_reference" }] }),
      artifact("TEST-final", "outgoing_messages", { status: "degraded", failure_code: "structured_output_invalid" })] });
  assert.ok(html.includes("source_refs"));
  assert.ok(html.includes("unavailable_reference"));
  assert.ok(html.includes("医生回复未放行"));
});

test("Style Trace shows frozen active version, expression IDs without act categories", () => {
  const html = render({
    style_profile_id: "TEST-style", style_profile_version: "TEST-style-v1",
    requested_style_profile_version: "TEST-style-v1",
    style_selection_source: "default", example_ids: ["TEST-example-1", "TEST-example-2"],
  });
  assert.ok(html.includes("TEST-style-v1"));
  assert.ok(html.includes("当前启用版本"));
  assert.ok(!html.includes("沟通行为"));
  assert.ok(html.includes("TEST-example-1"));
  assert.ok(html.includes("TEST-example-2"));
  assert.ok(!html.includes("风格资产已回退"));
});

test("asset fallback preserves the requested version and shows the actual default version", () => {
  const html = render({
    style_profile_version: "slimguard_default_v1", requested_style_profile_version: "TEST-missing-v1",
    style_selection_source: "fallback", style_fallback_reason: "profile_not_published",
    example_ids: [],
  });
  assert.ok(html.includes("slimguard_default_v1"));
  assert.ok(html.includes("TEST-missing-v1"));
  assert.ok(html.includes("所选版本未发布"));
  assert.ok(html.includes("本次未使用表达示例"));
  assert.ok(!html.includes("沟通行为"));
});

test("legacy traces distinguish absent metadata from an explicitly empty example selection", () => {
  const html = render({ style_profile_version: "TEST-legacy-v1" });
  assert.ok(html.includes("旧 Trace 未记录示例 ID"));
  assert.ok(html.includes("未记录"));
  assert.ok(!html.includes("本次未使用表达示例"));
});

test("latest repair resolution supplies the selected IDs", () => {
  const html = render({}, { artifacts: [
    artifact("TEST-initial", "style_resolution", {
      style_profile_version: "TEST-style-v1", example_ids: ["TEST-old-example"],
    }),
    artifact("TEST-repair", "style_resolution", {
      style_profile_version: "TEST-style-v1", example_ids: ["TEST-repair-example"],
    }),
  ] });
  assert.ok(html.includes("TEST-repair-example"));
  assert.ok(!html.includes("TEST-old-example"));
  assert.ok(!html.includes("沟通行为"));
});

test("style metadata does not reveal expression bodies, plans or raw conversations", () => {
  const html = render({}, { artifacts: [
    artifact("TEST-resolution", "style_resolution", {
      style_profile_version: "TEST-style-v1", example_ids: ["TEST-example"],
      examples: [{ text: "PRIVATE_TEST_EXAMPLE" }], raw_chat: "PRIVATE_TEST_CHAT",
    }),
    artifact("TEST-plan", "response_plan", {
      content_blocks: [{ kind: "fact", text: "PRIVATE_TEST_FACT", source_refs: [] }],
    }),
    artifact("TEST-response", "styled_response", {
      style_profile_version: "TEST-style-v1", text: "PRIVATE_TEST_REPLY", used_block_ids: [],
    }),
  ] });
  assert.ok(!html.includes("PRIVATE_TEST"));
  assert.ok(html.includes("TEST-example"));
});

test("style adoption labels distinguish an unadopted render from final output", () => {
  for (const final of [false, true]) {
    const html = render({ style_profile_version: "TEST-style-v1" }, {
      timeline: [{ sequence: 1, operation: "response_adopted", details: { artifact_id: "TEST-output", final } }],
    });
    assert.ok(html.includes(final ? "最终采用" : "未采用渲染"));
    assert.ok(html.includes(final ? "进入最终输出" : "未发送给用户"));
  }
});
