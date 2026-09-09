// Static safety checks for the administrator-owned style version pipeline.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

test("style version page keeps build, publish and activation as separate actions", async () => {
  const source = await readFile(
    new URL("../src/components/style/StyleIterationPage.tsx", import.meta.url),
    "utf8",
  );
  for (const expected of [
    "构建下一版本",
    "进入该版本 A/B 人评",
    "采纳并发布该版本",
    "全量启用该版本",
    "回滚到",
    "不会覆盖旧版本",
    "不暴露模型思维过程",
    "十二条之外的新场景",
  ]) assert.ok(source.includes(expected), expected);
  assert.doesNotMatch(source, /actor\s*:/);
});

test("style version mutations are authenticated, CSRF-protected and version-bound", async () => {
  const api = await readFile(new URL("../src/api.ts", import.meta.url), "utf8");
  for (const operation of [
    "createStyleIteration",
    "publishStyleIteration",
    "activateStyleVersion",
    "rollbackStyleVersion",
  ]) assert.ok(api.includes(operation), operation);
  assert.ok(api.includes("expected_revision"));
  assert.ok(api.includes('"X-SlimGuard-CSRF": "1"'));
});
