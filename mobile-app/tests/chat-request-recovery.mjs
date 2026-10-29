import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { runInNewContext } from 'node:vm';

import ts from 'typescript';

const require = createRequire(import.meta.url);
const appRoot = fileURLToPath(new URL('../', import.meta.url));

function loadMobileApi(fetchImpl, timeouts) {
  const filename = resolve(appRoot, 'src/lib/api.ts');
  const { outputText } = ts.transpileModule(readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
    fileName: filename,
  });
  const exports = {};
  runInNewContext(outputText, {
    exports,
    process: { env: { EXPO_PUBLIC_API_BASE_URL: 'https://api.test' } },
    fetch: fetchImpl,
    AbortController,
    URLSearchParams,
    Promise,
    Date,
    Error,
    setTimeout(callback, delayMs) {
      timeouts.push(delayMs);
      if (delayMs <= 5_000) callback();
      return 1;
    },
    clearTimeout() {},
    require(specifier) {
      if (specifier === '../shared/network/connection') return { connectionMonitor: { response() {}, failure() {} } };
      if (specifier === './session') {
        return {
          clearRefreshToken: async () => {},
          readRefreshToken: async () => 'refresh-token',
          saveRefreshToken: async () => {},
        };
      }
      if (specifier.startsWith('.')) throw new Error(`Unexpected API module dependency: ${specifier}`);
      return require(specifier);
    },
  });
  return exports.mobileApi;
}

function response(status, body) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

const payload = {
  text: '这是我的午餐',
  idempotency_key: 'image-chat-timeout-1',
  occurred_at: '2026-10-27T07:54:53.000Z',
};

test('reconciles a timed-out POST through the request-status endpoint', async () => {
  const calls = [];
  const timeouts = [];
  const api = loadMobileApi(async (url, init) => {
    calls.push({ url, init });
    if (init.method === 'POST') {
      const error = new Error('request timed out');
      error.name = 'AbortError';
      throw error;
    }
    return response(200, {
      request_id: 'request-1', status: 'succeeded', turn_id: 'turn-1', text: '记录好了',
      failure_code: null, replayed: true,
    });
  }, timeouts);
  api.accessToken = 'access-token';

  const result = await api.sendChat(payload);

  assert.equal(result.status, 'succeeded');
  assert.equal(calls.length, 2);
  assert.equal(calls[0].url, 'https://api.test/api/mobile/v1/chat/messages');
  assert.equal(calls[1].url, 'https://api.test/api/mobile/v1/chat/requests/image-chat-timeout-1');
  assert.equal(calls[1].init.method, 'GET');
  assert.ok(timeouts.includes(75_000));
});

test('polls a claimed running request until the server reports completion', async () => {
  const calls = [];
  const timeouts = [];
  const api = loadMobileApi(async (url, init) => {
    calls.push({ url, init });
    if (init.method === 'POST') {
      return response(200, {
        request_id: 'request-1', status: 'running', turn_id: 'turn-1', text: null,
        failure_code: null, replayed: true,
      });
    }
    return response(200, {
      request_id: 'request-1', status: 'succeeded', turn_id: 'turn-1', text: '记录好了',
      failure_code: null, replayed: true,
    });
  }, timeouts);
  api.accessToken = 'access-token';

  const result = await api.sendChat(payload);

  assert.equal(result.status, 'succeeded');
  assert.equal(calls.length, 2);
  assert.ok(timeouts.includes(75_000));
  assert.ok(timeouts.includes(10_000));
});
