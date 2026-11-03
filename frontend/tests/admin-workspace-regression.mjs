import assert from "node:assert/strict";
import { after, test } from "node:test";
import { readFile, readdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { createServer } from "vite";
import {
  user,
  style,
  version,
  totals,
  nutrition,
  call,
} from "./fixtures/admin-workspace.mjs";

const server = await createServer({
  root: fileURLToPath(new URL("../", import.meta.url)),
  appType: "custom",
  logLevel: "error",
  optimizeDeps: { noDiscovery: true, entries: [] },
  server: { middlewareMode: true, hmr: false, ws: false, watch: null },
});
after(() => server.close());
const { App } = await server.ssrLoadModule("/src/App.tsx");
const { LoginPage } = await server.ssrLoadModule("/src/app/AuthPages.tsx");
function render(path) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  for (const [key, value] of [
    [["admin-session"], { username: "operator" }],
    [["users", "", 0], { items: [user], total: 1 }],
    [["user", user.id], user],
    [["memories", user.id], []],
    [["records", user.id], { meals: [] }],
    [["routines", user.id], { jobs: [] }],
    [
      ["usage", "users", "offset=0&limit=20"],
      { items: [{ ...totals, user_id: user.id }], total: 1 },
    ],
    [
      ["usage", "users/demo-user/requests", "offset=0&limit=20"],
      { items: [], total: 0 },
    ],
    [["expression-styles"], { items: [style] }],
    [["expression-versions", "doctor"], { items: [version] }],
    [["expression-builds", "doctor", 0], { items: [], total: 0 }],
    [["nutrition-dashboard"], nutrition],
    [
      ["call-list", "all", ""],
      { items: [call], total: 1, offset: 0, limit: 30 },
    ],
  ])
    client.setQueryData(key, value);
  const html = renderToStaticMarkup(
    createElement(
      QueryClientProvider,
      { client },
      createElement(
        MemoryRouter,
        { initialEntries: [path] },
        createElement(App),
      ),
    ),
  );
  client.clear();
  return html;
}
for (const [path, active, title] of [
  ["/users", "调用过程", "调用过程"],
  ["/usage", "模型用量", "用户用量统计"],
  ["/users/directory", "用户管理", "用户中心"],
  ["/users/demo-user/memories", "用户管理", "用户记忆"],
  ["/users/demo-user/usage", "模型用量", "问答用量"],
  ["/styles", "表达风格", "风格列表"],
  ["/styles/doctor/build", "表达风格", "构建新版本"],
  ["/nutrition-knowledge", "营养知识库", "可检索向量"],
])
  test(`workspace and full navigation persist on ${path}`, () => {
    const html = render(path);
    assert.match(html, /class="app-shell"/);
    assert.match(html, /class="workspace-topbar"/);
    assert.ok(html.includes(title));
    const nav = html.match(/<nav aria-label="后台主导航">([\s\S]*?)<\/nav>/)[1];
    for (const label of [
      "调用过程",
      "模型用量",
      "用户管理",
      "表达风格",
      "营养知识库",
    ])
      assert.ok(nav.includes(label));
    assert.equal((nav.match(/aria-current="page"/g) || []).length, 1);
    assert.match(
      nav,
      new RegExp(`aria-current="page"[^>]*>[^]*?${active}</a>`),
    );
  });

test("login retains accessible credential controls in the shared theme", () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const html = renderToStaticMarkup(
    createElement(
      QueryClientProvider,
      { client },
      createElement(MemoryRouter, null, createElement(LoginPage)),
    ),
  );
  assert.match(html, /class="login-shell"/);
  assert.match(html, /称小二/);
  assert.match(html, /autoComplete="username"/i);
  assert.match(html, /type="password"/);
  assert.match(html, /autoComplete="current-password"/i);
  assert.match(html, /type="submit" disabled/);
  client.clear();
});

test("only the shared palette owns color literals; features cannot drift back to green themes", async () => {
  async function walk(dir) {
    const entries = await readdir(dir, { withFileTypes: true });
    return (
      await Promise.all(
        entries.map((e) =>
          e.isDirectory() ? walk(`${dir}/${e.name}`) : [`${dir}/${e.name}`],
        ),
      )
    ).flat();
  }
  const files = await walk(fileURLToPath(new URL("../src", import.meta.url)));
  for (const file of files.filter(
    (p) => p.endsWith(".css") && !p.endsWith("/tokens.css"),
  )) {
    const css = await readFile(file, "utf8");
    assert.doesNotMatch(
      css,
      /#[\da-f]{3,8}\b|rgba?\(|--green|light-dark\(/i,
      file,
    );
  }
  const css = await readFile(
    new URL("../src/app/shell.css", import.meta.url),
    "utf8",
  );
  assert.match(css, /132px minmax\(0, 1fr\)/);
  assert.doesNotMatch(css, /call-shell/);
});
