import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';

const require = createRequire(import.meta.url);
function load(file, doubles) {
  const source = readFileSync(new URL(`../src/${file}`, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true, target: ts.ScriptTarget.ES2022,
  } });
  const exports = {};
  runInNewContext(outputText, { exports, require: specifier => specifier in doubles ? doubles[specifier] : require(specifier) });
  return exports;
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const tick = () => new Promise(setImmediate);

function hook() {
  const states = [], calls = [];
  let cursor = 0, effects = [], user = 'u1', timezone = 'Asia/Shanghai', revision = 1;
  const { usePeriodProgress } = load('features/progress/usePeriodProgress.ts', {
    react: {
      useState(initial) {
        const index = cursor++;
        if (states.length <= index) states[index] = initial;
        return [states[index], value => { states[index] = typeof value === 'function' ? value(states[index]) : value; }];
      },
      useEffect(effect, deps) {
        const index = cursor++, previous = states[index];
        if (!previous || deps.some((value, i) => value !== previous.deps[i])) {
          effects.push(() => { previous?.cleanup?.(); states[index] = { deps, cleanup: effect() }; });
        }
      },
    },
    './api': { progressApi: { period(days) { const request = { days, ...deferred() }; calls.push(request); return request.promise; } } },
  });
  return {
    calls,
    read() { cursor = 0; effects = []; const result = usePeriodProgress(user, timezone, revision); effects.forEach(effect => effect()); return result; },
    user(value) { user = value; },
    timezone(value) { timezone = value; },
    refresh() { revision++; },
  };
}

test('switching periods ignores an older response and never shows counts from the wrong period', async () => {
  const view = hook();
  assert.equal(view.read().loading, true);
  assert.equal(view.calls[0].days, 7);
  view.read().setDays(30);
  assert.equal(view.read().data, null);
  assert.equal(view.calls[1].days, 30);
  view.calls[1].resolve({ days: 30, counts: { meal: 145 } });
  await tick();
  assert.equal(view.read().data.counts.meal, 145);
  view.calls[0].resolve({ days: 7, counts: { meal: 3 } });
  await tick();
  assert.equal(view.read().data.days, 30);
  assert.equal(view.read().data.counts.meal, 145);
});

test('failure is not fabricated zero data, and retry can recover a real empty result', async () => {
  const view = hook();
  view.read(); view.calls[0].reject(new Error('offline'));
  await tick();
  assert.equal(view.read().error, true);
  assert.equal(view.read().data, null);
  view.read().reload(); view.read();
  view.calls[1].resolve({ days: 7, counts: { meal: 0 } });
  await tick();
  assert.equal(view.read().error, false);
  assert.equal(view.read().data.counts.meal, 0);
});

test('refresh, timezone and user changes invalidate period data; an old user cannot leak into a new one', async () => {
  const view = hook();
  view.read(); view.calls[0].resolve({ days: 7, counts: { meal: 12 } });
  await tick();
  view.refresh(); view.read();
  assert.equal(view.calls.length, 2);
  view.timezone('America/New_York'); view.read();
  assert.equal(view.read().data, null);
  view.user('u2'); view.read();
  view.calls[1].resolve({ counts: { meal: 999 } });
  view.calls[2].resolve({ counts: { meal: 999 } });
  view.calls[3].resolve({ counts: { meal: 2 } });
  await tick();
  assert.equal(view.read().data.counts.meal, 2);
  view.user(undefined); assert.equal(view.read().data, null);
});
