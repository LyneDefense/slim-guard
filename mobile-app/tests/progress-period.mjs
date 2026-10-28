import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { runInNewContext } from 'node:vm';
import React from 'react';
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

function tree(node) {
  if (Array.isArray(node)) return node.flatMap(tree);
  return node && typeof node === 'object' && node.type ? [node, ...tree(node.props.children)] : [];
}
const native = {
  ...Object.fromEntries(['ActivityIndicator', 'Pressable', 'RefreshControl', 'ScrollView', 'Text', 'View'].map(name => [name, name])),
  StyleSheet: { create: value => value, hairlineWidth: 0.5 }, useWindowDimensions: () => ({ width: 390 }),
};
const theme = { colors: {}, radius: {}, spacing: {} };

test('progress screen offers 7/30-day periods, uses authoritative counts and has no recent activity list', () => {
  const changes = [];
  const summary = { from_date: '2026-10-22', to_date: '2026-10-28', counts: { weight: 9, body_fat: 0, meal: 145, exercise: 2 }, weights: [{ value: 79 }], body_fat: [] };
  const period = { days: 7, setDays: days => changes.push(days), data: summary, loading: false, error: false };
  const { ProgressScreen } = load('screens/ProgressScreen.tsx', {
    react: { __esModule: true, ...React, default: React, useState: () => ['weight', () => {}] },
    'react-native': native, 'react-native-safe-area-context': { SafeAreaView: 'SafeAreaView' },
    '@expo/vector-icons': { Ionicons: 'Icon' }, '../theme': theme,
    '../components/ui': { Card: 'Card', SectionTitle: 'SectionTitle' },
    '../context/AppContext': { useApp: () => ({ data: { user: { id: 'u1' }, routine: { timezone: 'Asia/Shanghai' } }, refresh() {}, loading: false }) },
    '../features/progress/TrendChart': { TrendChart: 'TrendChart' },
    '../features/progress/usePeriodProgress': { usePeriodProgress: () => period },
  });
  const nodes = tree(ProgressScreen({ openCoach() {} }));
  const segments = nodes.filter(node => typeof node.type === 'function' && node.type.name === 'Segment');
  segments.find(node => node.props.title === '近 30 天').props.onPress();
  assert.deepEqual(changes, [30]);
  assert.ok(nodes.some(node => node.type === 'SectionTitle' && node.props.title === '近 7 天的记录'));
  assert.equal(nodes.some(node => node.props.title === '最近活动' || node.props.title === '最近 60 条里的积累'), false);
  const stats = nodes.filter(node => typeof node.type === 'function' && node.type.name === 'Stat');
  assert.equal(stats.find(node => node.props.label === '体重').props.value, 9); // Not daily points.length.
  assert.equal(stats.find(node => node.props.label === '饮食').props.value, 145);
});

test('daily chart preserves calendar spacing, missing days and date-only labels without NaN', () => {
  const { TrendChart } = load('features/progress/TrendChart.tsx', {
    react: { __esModule: true, ...React, default: React }, 'react-native': native, '../../theme': theme,
    'react-native-svg': { __esModule: true, default: 'Svg', Circle: 'Circle', Line: 'Line', Path: 'Path' },
  });
  const points = [
    { record_id: 'a', on: '2026-10-22', value: 81 },
    { record_id: 'b', on: '2026-10-23', value: 80 },
    { record_id: 'c', on: '2026-10-28', value: 79 },
  ];
  const props = { points, fromDate: '2026-10-22', toDate: '2026-10-28', unit: 'kg' };
  const nodes = tree(TrendChart(props));
  const circles = nodes.filter(node => node.type === 'Circle');
  assert.equal(circles.length, 3); // Missing days are not zero-valued measurements.
  assert.ok(Math.abs(circles[1].props.cx - (12 + 286 / 6)) < 0.01);
  assert.equal(circles[2].props.cx, 298);
  assert.ok(nodes.some(node => node.type === 'Text' && node.props.children === '10/22'));
  assert.equal(JSON.stringify(nodes).includes('NaN'), false);
  const single = tree(TrendChart({ ...props, points: [points[2]] }));
  assert.equal(single.filter(node => node.type === 'Circle').length, 1);
  assert.ok(single.some(node => node.props.children === '本周期仅记录一天'));
});
