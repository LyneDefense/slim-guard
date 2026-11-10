// Synthetic UI/API acceptance data only. These tests never call a model.
import assert from "node:assert/strict";
import { after, test } from "node:test";
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
const { StyleDirectoryPage } = await server.ssrLoadModule(
  "/src/features/style-management/pages/StyleDirectoryPage.tsx",
);
const { StyleStudioPage, StudioEditor } = await server.ssrLoadModule(
  "/src/features/style-management/pages/StyleStudioPage.tsx",
);
const { styleStudioApi } = await server.ssrLoadModule(
  "/src/features/style-management/api.ts",
);
const { ApiError } = await server.ssrLoadModule("/src/api.ts");
const { ChatPanel } = await server.ssrLoadModule(
  "/src/features/style-management/components/ChatPanel.tsx",
);
const { CheckResult } = await server.ssrLoadModule(
  "/src/features/style-management/components/CheckResult.tsx",
);
const {
  validateExample,
  exampleFromChat,
  sameContent,
  publicationState,
  dateLabel,
} = await server.ssrLoadModule("/src/features/style-management/model.ts");
const content = {
  expression: "简短、克制，先回应眼前的事。",
  avoid: "避免机械鼓励。",
  examples: [
    {
      id: "example-1",
      title: "休息",
      messages: [
        { role: "user", content: "我今天不想运动。" },
        { role: "assistant", content: "今天休息。别一直休息。" },
        { role: "user", content: "明天呢？" },
        { role: "assistant", content: "明天出去走走。" },
      ],
    },
  ],
};
const draft = {
  revision: 3,
  content,
  content_hash: "hash-test",
  updated_at: "2026-11-10T01:00:00Z",
  actor: "TEST",
  source_version_id: "v1",
};
const active = {
  id: "v1",
  name: "医生 v1",
  status: "published",
  created_at: draft.updated_at,
  actor: "TEST",
  content,
  legacy: false,
  active: true,
};
const studio = { draft, active_version: active };
const style = {
  id: "doctor",
  name: "医生风格",
  description: "简短自然",
  is_default: true,
  active_version_id: "v1",
  example_count: 1,
  active_version: "医生 v1",
};
function render(path, studioData = studio, styleData = style) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  client.setQueryData(["expression-styles"], { items: [styleData] });
  client.setQueryData(["style-studio", "doctor"], studioData);
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
            element: createElement(StyleDirectoryPage),
          }),
          createElement(Route, {
            path: "/styles/:styleId",
            element: createElement(StyleStudioPage),
          }),
        ),
      ),
    ),
  );
  client.clear();
  return html;
}
async function withFetch(fn) {
  const previous = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({
      url,
      options,
      body: options.body ? JSON.parse(options.body) : null,
    });
    return Response.json({});
  };
  try {
    await fn(calls);
  } finally {
    globalThis.fetch = previous;
  }
}
test("style directory opens the role workbench and uses actual draft example counts", () => {
  const html = render("/styles");
  assert.ok(html.includes("医生风格"));
  assert.ok(html.includes("1 条示例"));
  assert.ok(html.includes('href="/styles/doctor"'));
  assert.doesNotMatch(html, /构建版本|追加纠正素材/);
});
test("workbench shows side by side role editing, independent examples, draft chat and release actions", () => {
  const html = render("/styles/doctor");
  for (const text of [
    "角色设定",
    "表达方式",
    "对话示例",
    "2 轮",
    "试聊",
    "当前草稿",
    "保存草稿",
    "发布并启用",
    "历史版本",
    "必要检查",
    "AI 帮我整理",
    "不写入真实用户记录",
    "与启用版本一致",
  ])
    assert.ok(html.includes(text), text);
  assert.doesNotMatch(html, /构建新版本|原回答|关联规则|追加纠正素材/);
});
test("unpublished roles can start editing and do not pretend to be enabled", () => {
  const html = render("/styles/doctor", { ...studio, active_version: null });
  assert.ok(html.includes("尚未启用"));
  assert.ok(html.includes("草稿未发布"));
  assert.doesNotMatch(html, /线上启用 ·/);
});
test("nondefault roles explain that publication only changes this role", () => {
  const html = render("/styles/doctor", studio, {
    ...style,
    is_default: false,
  });
  assert.ok(html.includes("非线上默认"));
  assert.ok(html.includes("当前线上默认风格不改变"));
  assert.doesNotMatch(html, /仅后续新回复使用新版本/);
});
test("role and example text are escaped", () => {
  const html = render("/styles/doctor", {
    ...studio,
    draft: {
      ...draft,
      content: {
        ...content,
        expression: "<script>PRIVATE</script>",
        examples: [
          {
            id: "x",
            title: "<img src=x>",
            messages: [
              { role: "user", content: "<script>U</script>" },
              { role: "assistant", content: "<script>A</script>" },
            ],
          },
        ],
      },
    },
  });
  assert.doesNotMatch(html, /<script>|<img src=x>/);
  assert.ok(html.includes("&lt;script&gt;"));
});
test("examples accept independent complete dialogues without correction material", () => {
  assert.equal(validateExample(content.examples[0]), null);
  assert.equal(
    validateExample({
      id: "one",
      title: "",
      messages: content.examples[0].messages.slice(0, 2),
    }),
    null,
  );
});
test("examples reject incomplete, nonalternating and blank exchanges", () => {
  for (const messages of [
    [],
    [{ role: "user", content: "hi" }],
    [
      { role: "assistant", content: "hi" },
      { role: "user", content: "hi" },
    ],
    [
      { role: "user", content: "hi" },
      { role: "assistant", content: " " },
    ],
  ])
    assert.ok(validateExample({ id: "bad", title: "", messages }));
});
test("saving an answer as example preserves its whole independent conversation and removes preview diagnostics", () => {
  const transcript = content.examples[0].messages.map((message, i) => ({
    ...message,
    ...(i % 2 ? { preview: { passed: true, checks: ["TEST"] } } : {}),
  }));
  const example = exampleFromChat(transcript, 3);
  assert.deepEqual(example.messages, content.examples[0].messages);
  assert.ok(example.id);
  assert.equal(
    example.messages.some((message) => "preview" in message),
    false,
  );
});
test("saving a long dialogue fails explicitly instead of silently truncating context", () => {
  const messages = Array.from({ length: 26 }, (_, i) => ({
    role: i % 2 ? "assistant" : "user",
    content: `TEST ${i}`,
  }));
  assert.throws(() => exampleFromChat(messages, 25), /12 轮上限/);
  assert.throws(() => exampleFromChat(messages, 24), /只能保存/);
  assert.throws(
    () =>
      exampleFromChat(
        [
          { role: "user", content: "hi" },
          { role: "assistant", content: "bad", preview: { passed: false } },
        ],
        1,
      ),
    /只能保存/,
  );
});
test("draft comparison includes examples and every message, so followup edits stale the preview", () => {
  assert.equal(sameContent(content, structuredClone(content)), true);
  const changed = structuredClone(content);
  changed.examples[0].messages[3].content = "换一句";
  assert.equal(sameContent(content, changed), false);
});
test("stale chat locks send and offers a restart while retaining the visible trajectory", () => {
  const html = renderToStaticMarkup(
    createElement(ChatPanel, {
      messages: content.examples[0].messages,
      pending: false,
      stale: true,
      error: "",
      onSend: async () => true,
      onRestart() {},
      onAdopt() {},
    }),
  );
  assert.ok(html.includes("已有对话使用的是旧设定"));
  assert.ok(html.includes("用新草稿重新试聊"));
  assert.match(
    html,
    /<button type="submit" class="studio-primary" disabled="">发送/,
  );
  assert.ok(html.includes("明天出去走走。"));
});
test("failed necessary checks expose issues without claiming publication eligibility", () => {
  const html = renderToStaticMarkup(
    createElement(CheckResult, {
      result: {
        passed: false,
        checks: [
          {
            source_id: "persona",
            role_passed: false,
            safety_passed: true,
            issues: [
              {
                dimension: "role",
                code: "unsafe_operation",
                explanation: "不能声称已记录",
              },
            ],
          },
        ],
        warnings: ["先修改示例"],
      },
    }),
  );
  assert.ok(html.includes("必要检查未通过"));
  assert.ok(html.includes("不能声称已记录"));
  assert.ok(html.includes("先修改示例"));
  assert.ok(html.includes("角色设定"));
  assert.ok(html.includes("职责边界：不能声称已记录"));
  assert.doesNotMatch(html, /<details open/);
  const main = html.replace(/<details>[\s\S]*?<\/details>/g, "");
  assert.ok(!main.includes("unsafe_operation"));
  assert.ok(!main.includes("source_id"));
});

test("successful checks report passed quantities with technical diagnostics collapsed", () => {
  const html = renderToStaticMarkup(
    createElement(CheckResult, {
      result: {
        passed: true,
        warnings: [],
        checks: [
          {
            source_id: "persona",
            role_passed: true,
            safety_passed: true,
            issues: [],
          },
        ],
      },
    }),
  );
  assert.match(html, /已通过 1 \/ 1 项/);
  assert.ok(html.includes("详细诊断"));
  assert.doesNotMatch(html, /<details open/);
});

test("database UTC timestamps without offsets display the same Shanghai time as explicit UTC", () => {
  const expected = dateLabel("2026-11-10T11:00:00Z");
  assert.equal(dateLabel("2026-11-10T11:00:00"), expected);
  assert.equal(dateLabel("2026-11-10T19:00:00+08:00"), expected);
  assert.ok(expected.includes("19:00:00"));
  assert.equal(dateLabel("not-a-date"), "not-a-date");
});
test("saving uses revision CAS and the complete independent example payload", async () =>
  withFetch(async (calls) => {
    await styleStudioApi.save("doctor", 3, content);
    assert.equal(calls[0].url, "/api/admin/styles/doctor/studio/draft");
    assert.equal(calls[0].options.method, "PUT");
    assert.deepEqual(calls[0].body, { revision: 3, content });
    assert.equal(
      new Headers(calls[0].options.headers).get("X-SlimGuard-CSRF"),
      "1",
    );
  }));
test("preview sends unsaved content and real prior turns without writing a user record", async () =>
  withFetch(async (calls) => {
    const unsaved = { ...content, expression: "新的未保存设定" };
    await styleStudioApi.preview(
      "doctor",
      unsaved,
      content.examples[0].messages,
      "那今天呢？",
    );
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, "/api/admin/styles/doctor/studio/preview");
    assert.deepEqual(calls[0].body, {
      content: unsaved,
      history: content.examples[0].messages,
      user_input: "那今天呢？",
    });
  }));
test("necessary checks bind the persisted revision and hash; publication binds that check", async () =>
  withFetch(async (calls) => {
    await styleStudioApi.check("doctor", draft);
    await styleStudioApi.publish("doctor", draft, { id: "check-TEST" });
    assert.deepEqual(calls[0].body, { revision: 3, content_hash: "hash-test" });
    assert.deepEqual(calls[1].body, {
      revision: 3,
      content_hash: "hash-test",
      check_id: "check-TEST",
    });
    assert.ok(
      calls.every(
        (call) =>
          new Headers(call.options.headers).get("X-SlimGuard-CSRF") === "1",
      ),
    );
  }));
test("AI organization requests a suggestion without implicitly saving or publishing", async () =>
  withFetch(async (calls) => {
    await styleStudioApi.organize("doctor", content);
    assert.equal(calls.length, 1);
    assert.ok(calls[0].url.endsWith("/studio/organize"));
    assert.deepEqual(calls[0].body, { content });
  }));
test("history and old material are read only; restoration only requests a new draft", async () =>
  withFetch(async (calls) => {
    await styleStudioApi.versions("doctor");
    await styleStudioApi.legacyExamples("doctor");
    await styleStudioApi.restore("doctor", 3, "v0");
    assert.ok(calls[0].url.endsWith("/studio/versions"));
    assert.ok(calls[1].url.endsWith("/studio/legacy-examples"));
    assert.equal(calls[0].body, null);
    assert.equal(calls[1].body, null);
    assert.deepEqual(calls[2].body, { revision: 3, version_id: "v0" });
  }));
test("draft conflicts keep status and explicit error code for preserving local edits", async () => {
  const previous = globalThis.fetch;
  globalThis.fetch = async () =>
    Response.json(
      { detail: "草稿已被其他管理员更新" },
      { status: 409, headers: { "X-SlimGuard-Error": "draft_conflict" } },
    );
  try {
    await assert.rejects(
      styleStudioApi.save("doctor", 3, content),
      (error) =>
        error instanceof ApiError &&
        error.status === 409 &&
        error.code === "draft_conflict",
    );
    assert.equal(content.expression, "简短、克制，先回应眼前的事。");
  } finally {
    globalThis.fetch = previous;
  }
});
test("model configuration conflicts have no draft conflict code", async () => {
  const previous = globalThis.fetch;
  globalThis.fetch = async () =>
    Response.json({ detail: "未配置模型" }, { status: 409 });
  try {
    await assert.rejects(
      styleStudioApi.check("doctor", draft),
      (error) =>
        error instanceof ApiError &&
        error.status === 409 &&
        error.code === undefined,
    );
  } finally {
    globalThis.fetch = previous;
  }
});

test("replaying an old publication uses the actual active version and never claims to switch back", () => {
  const current = { ...active, id: "v2", name: "医生 v2" };
  const result = publicationState(
    {
      version: active,
      active_version: current,
      draft,
      already_published: true,
    },
    true,
  );
  assert.equal(result.active.id, "v2");
  assert.ok(result.notice.includes("当前启用的是 医生 v2"));
  assert.ok(result.notice.includes("没有重新切回旧版"));
  assert.ok(!result.notice.includes("医生 v1 已发布并启用"));
});

test("publication success distinguishes default and nondefault activation", () => {
  const publication = {
    version: active,
    active_version: active,
    draft,
    already_published: false,
  };
  assert.match(
    publicationState(publication, true).notice,
    /仅后续新回复使用新版本/,
  );
  assert.match(
    publicationState(publication, false).notice,
    /当前线上默认风格未改变/,
  );
});
