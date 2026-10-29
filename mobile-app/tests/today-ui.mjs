import assert from 'node:assert/strict';
import { test } from 'node:test';
import { fixture, tick, tree } from './support/native-fixture.mjs';

const meal = (id, photos = []) => ({
  ref: { kind: 'meal', record_id: id, revision: 1 },
  occurrence: { precision: 'date', on: '2026-10-29', at: null, timezone: 'Asia/Shanghai' },
  content: { kind: 'meal', meal_type: 'lunch', photo_asset_ids: photos, foods: [{ item_id: 'f1', name: '米饭', portion: null }] },
  recognition_issues: [{ user_message: '如需更正，请告诉我具体菜名' }],
});
const text = view => view.nodes().filter(node => node.type === 'Text').map(node => node.props.children).flat().join('');
const named = (view, name) => view.nodes().filter(node => typeof node.type === 'function' && node.type.name === name);

test('home keeps the sticky brand outside scrolling, real values, missing values and existing actions', () => {
  let refreshed = 0;
  const data = { user: { nickname: '小林' }, memories: [], coach_profile: { profile: { target_weight_kg: 73, target_body_fat_percent: null } }, today: { current_weight_kg: 79.2, current_body_fat_percent: null, routine: { timezone: 'Asia/Shanghai' } } };
  const view = fixture('screens/TodayScreen.tsx', 'TodayScreen', { openCoach() {} }, {
    '../context/AppContext': { useApp: () => ({ data, loading: false, online: true, pending: [], refresh: () => refreshed++ }) },
    '../features/record-history/RecordHistory': { RecordHistory: 'RecordHistory' },
  });
  const scroll = view.find('ScrollView');
  assert.equal(tree(scroll.props.children).some(node => node.type.name === 'BrandHeader'), false);
  assert.equal(named(view, 'BrandHeader')[0].props.status, '已同步');
  assert.ok(text(view).includes('小林'));
  assert.deepEqual(named(view, 'MetricCard').map(node => node.props.value), ['79.2','—']);
  assert.equal(named(view, 'MetricCard')[0].props.target, '目标 73 kg');
  assert.equal(named(view, 'MetricCard')[1].props.target, '还没设置目标');
  scroll.props.refreshControl.props.onRefresh();
  assert.equal(refreshed, 1);
});

test('four quick actions have distinct reference colors and retain their coach drafts', () => {
  const calls = [];
  const view = fixture('features/today/TodayCards.tsx', 'TodayActions', { openCoach: value => calls.push(value) });
  const actions = view.nodes().filter(node => node.type === 'Pressable');
  assert.deepEqual(actions.map(node => node.props.accessibilityLabel), ['记体重','记饮食','记运动','聊一聊']);
  for (const action of actions) action.props.onPress();
  assert.deepEqual(calls, ['我刚量了体重：','我刚吃了','我刚运动了', undefined]);
  const backgrounds = actions.map(node => tree(node).find(node => node.type === 'View' && Array.isArray(node.props.style)).props.style[1].backgroundColor);
  assert.equal(new Set(backgrounds).size, 4);
});

test('both histories start joined/collapsed, expand independently and keep stable section keys', () => {
  const view = fixture('features/record-history/RecordHistory.tsx', 'RecordHistory', { timezone: 'Asia/Shanghai', refreshKey: 0, onChanged() {} }, {
    './MealEditor': { MealEditor: 'MealEditor' }, './useRecordSection': { useRecordSection: () => ({}) },
    './RecordRow': { RecordRow: 'RecordRow' },
  });
  let sections = named(view, 'RecordSection');
  assert.ok(sections.every(node => !node.props.expanded && node.props.joined));
  sections[0].props.onToggle();
  sections = named(view, 'RecordSection');
  assert.equal(sections[0].props.expanded, true); assert.equal(sections[1].props.expanded, false);
  assert.ok(sections.every(node => !node.props.joined));
  sections[1].props.onToggle();
  assert.ok(named(view, 'RecordSection').every(node => node.props.expanded));
  sections[0].props.onToggle();
  assert.equal(named(view, 'RecordSection')[1].props.expanded, true);
  assert.deepEqual(named(view, 'RecordSection').map(node => node.key), ['meal','exercise']);
});

test('meal row shows only dish data, meal badge and photo variant; no photo creates no placeholder', () => {
  for (const photos of [[],['p1','p2']]) {
    const view = fixture('features/record-history/RecordRow.tsx', 'RecordRow', { record: meal('m1', photos), timezone: 'Asia/Shanghai', opening: false, onEdit() {} }, {
      '../../components/PhotoGallery': { PhotoGallery: 'PhotoGallery' },
    });
    assert.ok(text(view).includes('10月29日午餐米饭'));
    assert.equal(text(view).includes('如需更正'), false);
    const gallery = view.find('PhotoGallery');
    assert.equal(!!gallery, photos.length > 0);
    if (gallery) { assert.equal(gallery.props.variant, 'record'); assert.equal(gallery.props.photos.length, 2); }
  }
});

test('photo-side food names wrap as whole dishes, with one accessible summary instead of an orphan character', () => {
  const record = meal('m1', ['p1']);
  record.content.foods = ['米饭','花菜炒五花肉','清炒白菜'].map((name, i) => ({ item_id: `f${i}`, name, portion: null }));
  const view = fixture('features/record-history/RecordRow.tsx', 'RecordRow', { record, timezone: 'Asia/Shanghai', opening: false }, {
    '../../components/PhotoGallery': { PhotoGallery: 'PhotoGallery' },
  });
  const names = view.find('View', props => props.accessibilityLabel === '米饭、花菜炒五花肉、清炒白菜');
  assert.equal(names.props.style.flexWrap, 'wrap');
  assert.equal(tree(names).filter(node => node.type === 'Text').length, 3);
});

test('exercise row formats timezone and steps, omits missing optional chips without fabricating data', () => {
  const record = { ref: {}, occurrence: { precision: 'datetime', at: '2026-10-29T10:20:00Z' }, content: { kind: 'exercise', activity_name: '快走', duration_minutes: 30, steps: 3200 } };
  const view = fixture('features/record-history/RecordRow.tsx', 'RecordRow', { record, timezone: 'Asia/Shanghai', opening: false }, {
    '../../components/PhotoGallery': { PhotoGallery: 'PhotoGallery' },
  });
  assert.ok(text(view).includes('10月29日 18:20快走30 分钟3,200 步'));
  record.content.duration_minutes = null; record.content.steps = null;
  assert.equal(named(view, 'AppIcon').length, 0);
  assert.equal(view.nodes().filter(node => node.type === 'Pressable').length, 0);
});

test('date-only, unknown and invalid dates never produce NaN or invented midnight', () => {
  const view = fixture('features/record-history/presentation.ts', 'recordDate');
  const { recordDate } = view.load(new URL('../src/features/record-history/presentation.ts', import.meta.url).pathname);
  assert.equal(recordDate(meal('m1').occurrence, 'America/Los_Angeles', true), '10月29日');
  assert.equal(recordDate({ at: 'invalid', on: null }, 'Asia/Shanghai', true), '日期未明确');
  assert.equal(recordDate({ at: null, on: null }, 'Asia/Shanghai', true), '日期未明确');
});

function sectionHook(api, expanded = true) {
  // The fixture mounts a hook as a pure function, keeping its state across explicit renders.
  const wrapper = fixture('features/record-history/useRecordSection.ts', 'useRecordSection', null, { './api': { recordHistoryApi: api } });
  const hook = wrapper.load(new URL('../src/features/record-history/useRecordSection.ts', import.meta.url).pathname).useRecordSection;
  let props = { kind: 'meal', timezone: 'Asia/Shanghai', refreshKey: 0, expanded };
  const view = wrapper.mount({ type: () => hook(props.kind, props.timezone, props.refreshKey, props.expanded), props: {} });
  return { render: view.render, update(next) { props = { ...props, ...next }; return view.render(); }, unmount: view.unmount };
}

test('history loads lazily and retains loaded pages through collapse/reopen', async () => {
  const calls = [];
  const view = sectionHook({ list: async (...args) => { calls.push(args); return { records: [meal('m1')], has_more: false, next_cursor: null }; } }, false);
  view.render(); assert.equal(calls.length, 0);
  view.update({ expanded: true }); await tick();
  assert.equal(view.render().page.records.length, 1);
  view.update({ expanded: false }); view.update({ expanded: true }); await tick();
  assert.equal(calls.length, 1);
});

test('pagination retry retains the failed cursor, deduplicates records and never drops earlier pages', async () => {
  const calls = []; let fail = true;
  const view = sectionHook({ list: async (_kind, _zone, cursor) => {
    calls.push(cursor);
    if (cursor && fail) { fail = false; throw new Error('network'); }
    return { records: cursor ? [meal('m1'),meal('m2')] : [meal('m1')], has_more: !cursor, next_cursor: cursor ? null : 'next' };
  } });
  view.render(); await tick();
  await view.render().load('next');
  assert.equal(view.render().page.records.length, 1); assert.ok(view.render().error);
  await view.render().retry();
  assert.deepEqual(calls, [undefined,'next','next']);
  assert.equal(view.render().page.records.length, 2); assert.equal(view.render().page.has_more, false);
});

test('history requests two real entries per page and forwards the opaque server cursor', async () => {
  const calls = [];
  const view = fixture('features/record-history/api.ts', 'recordHistoryApi', {}, {
    '../../lib/api': { mobileApi: { request: async url => { calls.push(url); return {}; } } },
  });
  const api = view.load(new URL('../src/features/record-history/api.ts', import.meta.url).pathname).recordHistoryApi;
  await api.list('meal', 'Asia/Shanghai', 'next+a/b=');
  const query = new URL(calls[0], 'https://enceladus.online').searchParams;
  assert.equal(query.get('limit'), '2'); assert.equal(query.get('cursor'), 'next+a/b=');
  assert.equal(query.get('timezone'), 'Asia/Shanghai'); assert.equal(query.get('kind'), 'meal');
});

test('refresh rejects stale list responses and ignores responses after unmount', async () => {
  const jobs = [];
  const view = sectionHook({ list: () => new Promise(resolve => jobs.push(resolve)) });
  view.render(); view.update({ refreshKey: 1 });
  jobs[1]({ records: [meal('fresh')], has_more: false }); await tick();
  jobs[0]({ records: [meal('stale')], has_more: false }); await tick();
  assert.equal(view.render().page.records[0].ref.record_id, 'fresh');
  view.update({ refreshKey: 2 }); view.unmount();
  jobs[2]({ records: [meal('unmounted')], has_more: false }); await tick();
  assert.equal(view.render().page, null);
});

test('opening an editor uses latest revision, blocks double taps and saves into the loaded page', async () => {
  let resolveEditor, opens = 0;
  const view = sectionHook({
    list: async () => ({ records: [meal('m1')], has_more: false }),
    meal: () => { opens++; return new Promise(resolve => { resolveEditor = resolve; }); },
  });
  view.render(); await tick();
  const first = view.render().openEditor(meal('m1'));
  void view.render().openEditor(meal('m1')); assert.equal(opens, 1);
  const fresh = { ...meal('m1'), ref: { ...meal('m1').ref, revision: 7 } };
  resolveEditor(fresh); await first;
  assert.equal(view.render().editing.ref.revision, 7);
  view.render().saved(fresh);
  assert.equal(view.render().editing, null); assert.equal(view.render().page.records[0].ref.revision, 7);
});

test('bottom tabs preserve all navigation callbacks, selection state and safe-area padding', () => {
  const calls = [];
  const view = fixture('shared/navigation/BottomTabBar.tsx', 'BottomTabBar', { selected: 'today', bottomInset: 34, onSelect: tab => calls.push(tab) });
  const tabs = view.nodes().filter(node => node.type === 'Pressable');
  assert.equal(tabs.length, 4); assert.equal(tabs[0].props.accessibilityState.selected, true);
  for (const tab of tabs) tab.props.onPress();
  assert.deepEqual(calls, ['today','coach','progress','profile']);
  assert.equal(view.find('View').props.style[1].paddingBottom, 34);
});

test('expanded section exposes real loading/error/empty states and only offers more when the API has more', () => {
  let loads = [], retries = 0;
  const state = { page: null, busy: true, error: null, editing: null, opening: null, load: cursor => loads.push(cursor), retry: () => retries++ };
  const parent = fixture('features/record-history/RecordHistory.tsx', 'RecordHistory', { timezone: 'Asia/Shanghai', refreshKey: 0, onChanged() {} }, {
    './MealEditor': { MealEditor: 'MealEditor' }, './useRecordSection': { useRecordSection: () => state }, './RecordRow': { RecordRow: 'RecordRow' },
  });
  named(parent, 'RecordSection')[0].props.onToggle();
  const view = parent.mount(named(parent, 'RecordSection')[0]);
  assert.ok(view.find('ActivityIndicator'));
  assert.equal(text(view).includes('暂无记录'), false);
  state.busy = false; state.error = '暂时无法加载，点击重试';
  const retry = view.find('Pressable', props => tree(props.children).some(node => node.props.children === state.error));
  retry.props.onPress(); assert.equal(retries, 1);
  assert.equal(text(view).includes('加载更早'), false);
  state.error = null; state.page = { records: [], has_more: false, next_cursor: null };
  assert.ok(text(view).includes('暂无记录'));
  assert.equal(text(view).includes('加载更早'), false);
  state.page = { records: [meal('m1')], has_more: true, next_cursor: 'next' };
  view.find('Pressable', props => props.accessibilityLabel === '加载更早的饮食记录').props.onPress();
  assert.deepEqual(loads, ['next']);
});
