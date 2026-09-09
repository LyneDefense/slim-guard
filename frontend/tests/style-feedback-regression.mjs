// Invented TEST fixture only. No production conversation or real feedback is present.
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

const { StyleFeedbackRecord } = await server.ssrLoadModule(
  "/src/components/style/StyleFeedbackPage.tsx",
);

const fixture = {
  feedback_id: "TEST-feedback-id",
  profile_version: "TEST-style-v3",
  communication_act: null,
  scenario: "TEST 十二条之外的合成新场景。",
  user_message: "TEST 用户消息。",
  agent_response: "TEST 当前回复。",
  desired_response: "TEST 期望回复。",
  guidance_note: "TEST 去掉正式铺垫。",
  actor: "TEST-admin",
  content_sha256: "a".repeat(64),
  created_at: "2026-09-08T08:00:00Z",
};

test("feedback history shows scenario and exact current-to-desired correction", () => {
  const html = renderToStaticMarkup(createElement(StyleFeedbackRecord, { value: fixture }));
  for (const expected of [
    "待归类",
    "TEST-style-v3",
    "TEST 十二条之外的合成新场景。",
    "TEST 用户消息。",
    "TEST 当前回复。",
    "TEST 期望回复。",
    "TEST 去掉正式铺垫。",
    "TEST-admin",
    "a".repeat(64),
  ]) assert.ok(html.includes(expected), expected);
});

test("feedback form explains version boundary and requires privacy confirmations", async () => {
  const source = await readFile(
    new URL("../src/components/style/StyleFeedbackPage.tsx", import.meta.url),
    "utf8",
  );
  for (const expected of [
    "提交后不会立即改变线上 agent",
    "用户说了什么",
    "Agent 当时的回复",
    "你希望它怎么回复",
    "12 条之外的新场景",
    "deidentified_confirmed: true",
    "expression_only_confirmed: true",
    "不会把测试内容写入用户 Memory 或营养知识库",
    "追加反馈后，怎样生成下一版本",
    "进入“风格版本”页面选择来源版本并点击“构建下一版本”",
    "整套人评全部接受后",
  ]) assert.ok(source.includes(expected), expected);
  assert.doesNotMatch(source, /name=["']actor["']/);
});

test("feedback API keeps actor server-owned and sends CSRF on append", async () => {
  const [apiSource, routeSource] = await Promise.all([
    readFile(new URL("../src/api.ts", import.meta.url), "utf8"),
    readFile(
      new URL("../../src/slim_guard/api/style_feedback_routes.py", import.meta.url),
      "utf8",
    ),
  ]);
  assert.match(apiSource, /appendStyleFeedback/);
  assert.match(apiSource, /"X-SlimGuard-CSRF": "1"/);
  assert.match(routeSource, /actor=principal\.username/);
  assert.doesNotMatch(routeSource, /payload\.actor/);
});

test("admin navigation exposes the correction entry separately from A-B review", async () => {
  const appSource = await readFile(new URL("../src/App.tsx", import.meta.url), "utf8");
  assert.match(appSource, /to="\/style-feedback"/);
  assert.match(appSource, /path="style-feedback" element=\{<StyleFeedbackPage \/>\}/);
  assert.match(appSource, /path="style-iterations" element=\{<StyleIterationPage \/>\}/);
  assert.ok(appSource.includes("风格 A/B 人评"));
  assert.ok(appSource.includes("风格纠正"));
  assert.ok(appSource.includes("风格版本"));
});
