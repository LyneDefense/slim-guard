import assert from "node:assert/strict";
import { after, test } from "node:test";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

const server = await createServer({
  root: fileURLToPath(new URL("../", import.meta.url)), appType: "custom", logLevel: "error",
  optimizeDeps: { noDiscovery: true, entries: [] },
  server: { middlewareMode: true, hmr: false, ws: false, watch: null },
});
after(() => server.close());
const { UsagePage, UsageDetailPage } = await server.ssrLoadModule("/src/features/usage/UsagePage.tsx");
const totals = { calls: 2, invocations: 1, input_tokens: 10, output_tokens: 5, total_tokens: 15, unknown_calls: 1, failed_calls: 1, running_calls: 0 };
function render(path, route, component, key, data) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  client.setQueryData(key, data);
  const html = renderToStaticMarkup(createElement(QueryClientProvider, { client },
    createElement(MemoryRouter, { initialEntries: [path] }, createElement(Routes, null,
      createElement(Route, { path: route, element: createElement(component) })))));
  client.clear();
  return html;
}
test("usage list keeps per-user routes and does not fabricate zero for unmetered turns", () => {
  const html = render("/users/u/usage", "/users/:userId/usage", UsagePage,
    ["usage", "users/u/requests", "offset=0&limit=20"], {
      items: [{ ...totals, calls: 0, id: "empty", started_at: "2026-09-23T00:00:00Z" }], total: 1,
    });
  assert.match(html, /未记录调用/);
  assert.match(html, /\/users\/u\/usage\/empty/);
  assert.match(html, /后续确认是新问答/);
});
test("usage detail exposes agent totals, individual unknown calls, and trace link", () => {
  const html = render("/users/u/usage/t", "/users/:userId/usage/:requestId", UsageDetailPage,
    ["usage-detail", "users/u/requests/t", 0], {
      summary: totals, executions: 2, trace_id: "trace", coverage: "外部 Mem0 用量未知",
      agents: [{ ...totals, agent_role: "nutrition_expert", executions: 2 }], groups: [],
      items: [{ id: "call", agent_role: "nutrition_expert", component: "agent_generation", model: "test-model", total_tokens: null, input_tokens: null, output_tokens: null, status: "failed", started_at: "2026-09-23T00:00:00Z", parent_invocation_id: "core-i", invocation_id: "nutrition-i" }], total: 1,
    });
  assert.match(html, /营养 Agent/);
  assert.match(html, /未知 Token/);
  assert.match(html, /core-i/);
  assert.match(html, /\/users\/u\/traces\/trace/);
  assert.match(html, /包括没有发起模型请求的执行/);
});
