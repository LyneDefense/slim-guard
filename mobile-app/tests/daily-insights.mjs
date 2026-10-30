import assert from 'node:assert/strict';
import { test } from 'node:test';
import { fixture, tick } from './support/native-fixture.mjs';

const day = '2026-10-30';
const evidence = { id: 'record:meal:1:r1', kind: 'meal', label: '米饭、白菜', today: true, data: {} };
const ready = { date: day, timezone: 'Asia/Shanghai', status: 'ready', headline: '今天午饭想吃什么？',
  summary_state: 'ready', summary: '今天记录了米饭和白菜。', as_of: '2026-10-30T10:00:00Z',
  generated_at: '2026-10-30T10:00:02Z', evidence: [evidence], failure_code: null };
const text = view => view.nodes().filter(node => node.type === 'Text').map(node => node.props.children).flat().join('');
const settled = async () => { await tick(); await tick(); };
const deferred = () => { let resolve; const promise = new Promise(yes => { resolve = yes; }); return { promise, resolve }; };

function hook({ get = async () => ready, retry = async () => ready, userId = 'u1' } = {}) {
  const calls = [], timers = new Map(); let clock = '2026-10-30T10:00:00Z', foreground, serial = 0;
  class Clock extends Date { constructor(value) { super(value === undefined ? clock : value); } }
  const view = fixture('features/daily-insights/useDailyInsights.ts', 'useDailyInsights', {}, {
    './api': { dailyInsightsApi: { get: () => { calls.push('get'); return get(); }, retry: () => { calls.push('retry'); return retry(); } } },
    $globals: { Date: Clock, setTimeout: (fn, ms) => { const id = ++serial; timers.set(id, { fn, ms }); return id; }, clearTimeout: id => timers.delete(id) },
    $appState: (_name, fn) => { foreground = fn; return { remove() {} }; },
  });
  const original = view.load(new URL('../src/features/daily-insights/useDailyInsights.ts', import.meta.url).pathname);
  let props = { userId, timezone: 'Asia/Shanghai', refreshKey: 1 };
  const mounted = view.mount({ type: () => original.useDailyInsights(props.userId, props.timezone, props.refreshKey), props: {} });
  const advance = async () => { const [id, timer] = timers.entries().next().value; timers.delete(id); timer.fn(); await settled(); };
  return { view: mounted, calls, timers, advance, foreground: () => foreground('active'),
    change: value => { props = { ...props, ...value }; return mounted.render(); }, clock: value => { clock = value; } };
}

test('home content loads independently and polls pending work, not the whole dashboard or chat', async () => {
  let value = { ...ready, status: 'pending', headline: null, summary: null, summary_state: 'pending' };
  const state = hook({ get: async () => value }); state.view.render(); await settled();
  assert.equal(state.view.render().view.summary_state, 'pending'); assert.equal(state.calls.length, 1);
  assert.equal([...state.timers.values()][0].ms, 3000);
  value = ready; await state.advance();
  assert.equal(state.view.render().view.summary, ready.summary);
  assert.equal([...state.timers.values()][0].ms, 45000);
  state.view.unmount(); assert.equal(state.timers.size, 0);
});

test('late responses cannot expose another account or overwrite refreshed record content', async () => {
  const first = deferred(); let calls = 0;
  const state = hook({ get: async () => ++calls === 1 ? first.promise : { ...ready, summary: '更新后的内容' } });
  state.view.render(); await settled(); state.change({ userId: 'u2', refreshKey: 2 }); await settled();
  first.resolve({ ...ready, summary: '旧账号的内容' }); await settled();
  assert.equal(state.view.render().view.summary, '更新后的内容'); state.view.unmount();
});

test('source refresh hides the obsolete summary until the server checks its references', async () => {
  const wait = deferred(); let calls = 0;
  const state = hook({ get: async () => ++calls === 1 ? ready : wait.promise });
  state.view.render(); await settled(); assert.equal(state.view.render().verifying, false);
  state.change({ refreshKey: 2 }); assert.equal(state.view.render().verifying, true);
  wait.resolve({ ...ready, status: 'pending', summary: null, summary_state: 'pending' }); await settled();
  assert.equal(state.view.render().view.summary, null); state.view.unmount();
});

test('failed reads stay local to this feature, can retry and do not start a chat request', async () => {
  const state = hook({ get: async () => { throw new Error('timeout'); } });
  state.view.render(); await settled(); assert.equal(state.view.render().error, '暂时无法加载今日总结');
  await state.view.render().retry(); assert.equal(state.calls.at(-1), 'retry');
  assert.equal(state.view.render().error, null); state.view.unmount();
});

test('a successful manual record edit immediately hides the old summary even if dashboard refresh fails', async () => {
  const wait = deferred(); let calls = 0;
  const state = hook({ get: async () => ++calls === 1 ? ready : wait.promise });
  state.view.render(); await settled();
  const request = state.view.render().refresh();
  assert.equal(state.view.render().verifying, true);
  wait.resolve({ ...ready, status: 'pending', summary: null, summary_state: 'pending' });
  await request; assert.equal(state.view.render().view.summary, null);
  state.view.unmount();
});

test('midnight drops yesterday rather than showing it as today, and foreground rechecks data', async () => {
  let value = ready;
  const state = hook({ get: async () => value }); state.view.render(); await settled();
  state.clock('2026-10-30T16:01:00Z'); assert.equal(state.view.render().view, null);
  value = { ...ready, date: '2026-10-31', summary: null, summary_state: 'empty' };
  state.foreground(); await settled(); assert.equal(state.view.render().view.date, '2026-10-31');
  state.view.unmount();
});

test('summary presents serious model copy and a real cutoff; reference rows expand and collapse', () => {
  const view = fixture('features/daily-insights/DailySummaryCard.tsx', 'DailySummaryCard', {
    view: ready, busy: false, verifying: false, error: null, onRetry() {},
  });
  assert.ok(text(view).includes(ready.summary)); assert.ok(text(view).includes('18:00'));
  view.find('Pressable', props => props.accessibilityLabel === '查看今日总结依据').props.onPress();
  assert.ok(text(view).includes('米饭、白菜'));
  view.find('Pressable', props => props.accessibilityLabel === '查看今日总结依据').props.onPress();
  assert.equal(text(view).includes('米饭、白菜'), false);
});

test('empty, pending, failure and source-check states never present an old daily assessment', () => {
  for (const [state, verifying, error, expected] of [
    ['empty', false, null, '今天还没有可回顾的记录'],
    ['pending', false, null, '教练正在整理'],
    ['failed', false, null, '暂未生成'],
    ['ready', true, null, '核对最新记录'],
    ['ready', false, '请求失败', '请求失败'],
  ]) {
    const view = fixture('features/daily-insights/DailySummaryCard.tsx', 'DailySummaryCard', {
      view: { ...ready, summary_state: state }, busy: false, verifying, error, onRetry() {},
    });
    assert.equal(text(view).includes(ready.summary), false); assert.ok(text(view).includes(expected));
  }
});

test('homepage uses generated headline, preserves quick actions and places summary before history', () => {
  const data = { user: { id: 'u1', nickname: '小林' }, memories: [], coach_profile: { profile: null },
    today: { routine: { timezone: 'Asia/Shanghai' } } };
  const view = fixture('screens/TodayScreen.tsx', 'TodayScreen', { openCoach() {} }, {
    '../context/AppContext': { useApp: () => ({ data, pending: [], online: true, loading: false, refresh() {} }) },
    '../features/daily-insights/useDailyInsights': { useDailyInsights: () => ({ view: ready, verifying: false, busy: false, error: null, retry() {} }) },
    '../features/record-history/RecordHistory': { RecordHistory: 'RecordHistory' },
  });
  assert.ok(text(view).includes(ready.headline)); assert.equal(text(view).includes('不用完美'), false);
  const components = view.nodes().map(node => typeof node.type === 'function' ? node.type.name : node.type);
  assert.ok(components.indexOf('TodayActions') < components.indexOf('DailySummaryCard'));
  assert.ok(components.indexOf('DailySummaryCard') < components.indexOf('RecordHistory'));
});
