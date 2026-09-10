import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

const [page, api, app, routes, repository] = await Promise.all([
  readFile(new URL("../src/components/nutrition/NutritionKnowledgePage.tsx", import.meta.url), "utf8"),
  readFile(new URL("../src/api.ts", import.meta.url), "utf8"),
  readFile(new URL("../src/App.tsx", import.meta.url), "utf8"),
  readFile(new URL("../../src/slim_guard/api/nutrition_knowledge_routes.py", import.meta.url), "utf8"),
  readFile(new URL("../../src/slim_guard/nutrition_rag/repository.py", import.meta.url), "utf8"),
]);

test("admin navigation exposes the nutrition knowledge control plane", () => {
  assert.match(app, /to="\/nutrition-knowledge"/);
  assert.match(app, /path="nutrition-knowledge" element=\{<NutritionKnowledgePage \/>\}/);
  for (const label of ["总览", "资料源", "后台任务", "检索实验室", "语料版本", "评测集"]) {
    assert.ok(page.includes(label), label);
  }
});

test("source workflow exposes immutable COS input and all governance reviews", () => {
  for (const expected of [
    "上传文件", "粘贴正文", "远程 URL", "下载 COS 原始文件",
    "内容准确性", "适用范围", "版权与使用权", "批准此项", "撤销结论",
    "解析章节", "检索切片",
  ]) assert.ok(page.includes(expected), expected);
  assert.match(api, /FormData/);
  assert.match(api, /X-SlimGuard-CSRF/);
  assert.match(routes, /principal\.username/);
});

test("retrieval lab shows every hybrid channel and release operations are explicit", () => {
  for (const expected of [
    "Dense：语义向量", "Lexical：中文全文", "Phrase：短语命中",
    "RRF：融合", "Rerank：最终相关性", "把这次结果追加为回归评测 Case",
    "全量启用", "回滚到此版本",
  ]) assert.ok(page.includes(expected), expected);
  assert.match(api, /Idempotency-Key/);
  assert.match(routes, /retrieval-lab\/runs/);
});

test("evaluation UI and backend preserve frozen quality gates", () => {
  for (const expected of [
    "至少需要 100 条", "不少于 25%", "Recall@5 ≥ 90%", "引用完整率 100%",
  ]) assert.ok(page.includes(expected), expected);
  assert.match(repository, /len\(normalized\) >= 100/);
  assert.match(repository, /negative_count \/ len\(normalized\) >= 0\.25/);
  assert.match(repository, /metrics\.get\("gates_passed"\) is not True/);
});
