import assert from 'node:assert/strict';
import { test } from 'node:test';
import { fixture, tree } from './support/native-fixture.mjs';

const fromDate = '2026-10-23', toDate = '2026-10-29';
const points = [['23',79.6],['24',79.4],['26',79.5],['27',79.2],['29',79]].map(([day, value]) => ({ record_id: day, on: `2026-10-${day}`, value, unit: 'kg' }));
const summary = { days: 7, from_date: fromDate, to_date: toDate, timezone: 'Asia/Shanghai', weights: points, body_fat: [], counts: { weight: 9, body_fat: 0, meal: 145, exercise: 3 } };
const text = view => view.nodes().filter(node => node.type === 'Text').flatMap(node => node.props.children).filter(value => typeof value !== 'object').join('');
const named = (view, name) => view.nodes().filter(node => typeof node.type === 'function' && node.type.name === name);
const presentationView = fixture('features/progress/presentation.ts', 'shortDate', fromDate);
const presentation = presentationView.load(new URL('../src/features/progress/presentation.ts', import.meta.url).pathname);

test('calendar dates preserve missing days, leap day and DST without timezone shifting', () => {
  assert.equal(presentation.periodDates(fromDate, toDate).length, 7);
  assert.equal(presentation.weekday(toDate), '周四');
  assert.equal(presentation.weekday('2026-10-23'), '周五');
  assert.equal(presentation.periodDates('2024-02-28','2024-03-01').length, 3);
  assert.equal(presentation.periodDates('2026-10-31','2026-11-02').length, 3);
  assert.equal(presentation.shortDate('2026-03-08'), '03/08');
});

test('invalid dates never render NaN, roll over an invalid day or loop backward', () => {
  for (const on of ['invalid','2026-02-30','2026-13-01','2026-1-01']) {
    assert.equal(presentation.shortDate(on), '日期未明确');
    assert.equal(presentation.weekday(on), '');
    assert.equal(presentation.periodDates(on,toDate).length, 0);
  }
  assert.equal(presentation.periodDates(toDate,fromDate).length, 0);
});

test('cross-month and cross-year calendar captions keep both endpoints', () => {
  assert.equal(presentation.calendarCaption(fromDate,toDate), '10月 · 有测量的日子');
  assert.equal(presentation.calendarCaption('2026-09-30',toDate), '9 — 10月 · 有测量的日子');
  assert.equal(presentation.calendarCaption('2026-12-30','2027-01-05'), '2026/12 — 2027/1 · 有测量的日子');
});

test('measurement presentation sorts copies, rejects invalid/out-of-period values and never fabricates days', () => {
  const source = [...points].reverse();
  const result = presentation.measurementDates([...source, { on: '2026-10-30', value: 1 }, { on: 'invalid', value: 1 }, { on: fromDate, value: NaN }],fromDate,toDate);
  assert.deepEqual(Array.from(result, point => point.on), points.map(point => point.on));
  assert.equal(source[0].on, toDate);
  assert.equal(result.length, 5);
});

test('first-to-last changes handle gain, loss, no change, empty and single measurement honestly', () => {
  assert.equal(presentation.periodChange(points,'weight'), '−0.6 kg');
  assert.equal(presentation.periodChange([{ value: 79 },{ value: 80 }],'weight'), '+1.0 kg');
  assert.equal(presentation.periodChange([{ value: 79 },{ value: 79 }],'weight'), '0.0 kg');
  assert.equal(presentation.periodChange([],'bodyFat'), '暂无体脂记录');
  assert.equal(presentation.periodChange([points[0]],'weight'), '仅有一天测量');
  assert.equal(presentation.periodChange([{ value: 25.1 },{ value: 24.8 }],'bodyFat'), '−0.3 个百分点');
});

test('summary uses actual endpoint values, never a previous-period comparison or fabricated zero', () => {
  const view = fixture('features/progress/PeriodSummary.tsx','PeriodSummary',{ points, metric: 'weight' });
  assert.deepEqual(named(view,'SummaryValue').map(node => node.props.value), [79.6,79]);
  assert.ok(text(view).includes('本期首末变化−0.6 kg'));
  view.updateProps({ points: [] });
  assert.ok(named(view,'SummaryValue').every(node => node.props.value === undefined));
  assert.ok(text(view).includes('暂无体重记录'));
  const emptyValue = view.mount(named(view,'SummaryValue')[0]);
  assert.equal(text(emptyValue), '本期首次—kg');
});

function screen(periodOverrides = {}, appOverrides = {}) {
  const calls = [];
  const period = { days: 7, setDays: days => calls.push(days), data: summary, loading: false, error: false, reload: () => calls.push('reload'), ...periodOverrides };
  const app = { data: { user: { id: 'u1' }, routine: { timezone: 'Asia/Shanghai' } }, online: true, pending: [], loading: false, refresh: () => calls.push('refresh'), ...appOverrides };
  const view = fixture('screens/ProgressScreen.tsx','ProgressScreen',{ openCoach() {} }, {
    '../context/AppContext': { useApp: () => app }, '../features/progress/usePeriodProgress': { usePeriodProgress: () => period },
  });
  return { view, calls, period, app };
}

test('screen uses sticky brand, 7/30-day controls and real data with no obsolete activity/chart', () => {
  const { view, calls } = screen();
  const scroll = view.find('ScrollView');
  assert.equal(tree(scroll.props.children).some(node => node.type.name === 'BrandHeader'), false);
  assert.equal(named(view,'BrandHeader')[0].props.status, '已同步');
  assert.ok(text(view).includes('记录与变化'));
  view.find('Pressable', props => props.accessibilityLabel === '近 30 天').props.onPress();
  assert.deepEqual(calls, [30]);
  assert.equal(named(view,'PeriodSummary')[0].props.points.length, 5);
  assert.equal(named(view,'PeriodCounts')[0].props.counts.weight, 9);
  assert.equal(named(view,'PeriodCounts')[0].props.counts.meal, 145);
  assert.equal(text(view).includes('最近活动'), false);
  assert.equal(named(view,'TrendChart').length, 0);
  scroll.props.refreshControl.props.onRefresh();
  assert.deepEqual(calls, [30,'refresh','reload']);
});

test('metric switching updates both comparison and journal, and remount key resets the old selection', () => {
  const { view } = screen();
  const original = named(view,'MeasurementJournal')[0];
  original.props.onMetricChange('bodyFat');
  assert.equal(named(view,'PeriodSummary')[0].props.metric, 'bodyFat');
  assert.equal(named(view,'PeriodSummary')[0].props.points.length, 0);
  const updated = named(view,'MeasurementJournal')[0];
  assert.equal(updated.props.metric, 'bodyFat');
  assert.notEqual(updated.key,original.key);
});

test('loading/failure do not masquerade as zero stats; retry and offline status are usable', () => {
  const { view, calls, period, app } = screen({ data: null, loading: true });
  assert.ok(view.find('ActivityIndicator'));
  assert.equal(named(view,'PeriodCounts').length, 0);
  period.loading = false; period.error = true;
  assert.ok(text(view).includes('暂时无法加载统计'));
  view.find('Pressable', props => props.onPress === period.reload).props.onPress();
  assert.deepEqual(calls, ['reload']);
  app.online = false; app.pending = [{}];
  assert.equal(named(view,'BrandHeader')[0].props.status, '1 条待发送');
});

function journal(overrides = {}) {
  const calls = [];
  const view = fixture('features/progress/MeasurementJournal.tsx','MeasurementJournal', {
    points, metric: 'weight', fromDate, toDate, onMetricChange: value => calls.push(value), openCoach: draft => calls.push(draft), ...overrides,
  });
  return { view, calls };
}

test('journal ribbon includes all dates but only real measurement days have filled dots', () => {
  const { view } = journal();
  const days = view.nodes().filter(node => node.type === 'Pressable' && node.props.accessibilityLabel?.startsWith('2026-'));
  assert.equal(days.length, 7);
  assert.equal(days.filter(node => node.props.accessibilityLabel.includes('有体重测量')).length, 5);
  assert.equal(days.filter(node => node.props.accessibilityLabel.includes('未测量')).length, 2);
  assert.equal(days.find(node => node.props.accessibilityState.selected).props.accessibilityLabel, '2026-10-29 周四，有体重测量');
});

test('journal shows 3 newest days, expands real data and can collapse without an invented request', () => {
  const { view } = journal();
  const rows = () => view.nodes().filter(node => node.type === 'Pressable' && node.props.accessibilityLabel?.startsWith('查看 2026-'));
  assert.deepEqual(rows().map(node => node.props.accessibilityLabel), ['查看 2026-10-29 体重 79.0 kg','查看 2026-10-27 体重 79.2 kg','查看 2026-10-26 体重 79.5 kg']);
  view.find('Pressable', props => props.accessibilityLabel === '查看全部 5 天的测量').props.onPress();
  assert.equal(rows().length, 5);
  view.find('Pressable', props => props.accessibilityLabel === '收起测量明细').props.onPress();
  assert.equal(rows().length, 3);
});

test('tapping a row or missing date opens the correct date detail and keeps days without data empty', () => {
  const { view } = journal();
  view.find('Pressable', props => props.accessibilityLabel === '查看 2026-10-27 体重 79.2 kg').props.onPress();
  let detail = named(view,'MeasurementDetail')[0];
  assert.equal(detail.props.point.value,79.2);
  detail.props.onClose(); assert.equal(named(view,'MeasurementDetail').length,0);
  view.find('Pressable', props => props.accessibilityLabel === '2026-10-25 周日，未测量').props.onPress();
  detail = named(view,'MeasurementDetail')[0];
  assert.equal(detail.props.on,'2026-10-25'); assert.equal(detail.props.point,undefined);
});

test('weight and fat record actions forward the right existing coach draft', () => {
  for (const [metric,label,draft] of [['weight','体重','我刚量了体重：'],['bodyFat','体脂','我刚测了体脂：']]) {
    const { view, calls } = journal({ metric });
    view.find('Pressable', props => props.accessibilityLabel === `记一笔${label}`).props.onPress();
    assert.deepEqual(calls,[draft]);
    view.find('Pressable', props => props.accessibilityLabel === '体脂').props.onPress();
    assert.equal(calls[1],'bodyFat');
  }
});

test('30-day ribbon includes a cross-month range with a scroll hint; empty state invents no records', () => {
  const { view } = journal({ fromDate: '2026-09-30', points: [] });
  assert.equal(view.nodes().filter(node => node.type === 'Pressable' && node.props.accessibilityLabel?.startsWith('2026-')).length,30);
  assert.ok(text(view).includes('左右滑动查看日期'));
  assert.ok(text(view).includes('这段时间还没有体重记录'));
  assert.equal(text(view).includes('切换到近 30 天'),false);
  assert.equal(named(view,'MeasurementDetail').length,0);
});

test('detail is dismissible with bottom controls and missing-day record includes the actual date', () => {
  const calls = [];
  const view = fixture('features/progress/MeasurementDetail.tsx','MeasurementDetail', { on: '2026-10-25', metric: 'bodyFat', onClose: () => calls.push('close'), openCoach: value => calls.push(value) });
  assert.ok(text(view).includes('暂无记录'));
  const button = view.nodes().find(node => node.type === 'Pressable' && tree(node).some(child => child.props.children === '补记这一天'));
  button.props.onPress();
  assert.deepEqual(calls,['close','我想记录 2026-10-25 的体脂：']);
  view.find('Modal').props.onRequestClose(); assert.equal(calls.at(-1),'close');
  view.updateProps({ point: { ...points[0], value: 24.8 } });
  assert.ok(text(view).includes('24.8 %'));
  assert.equal(text(view).includes('补记这一天'), false);
});

test('period statistics use backend record counts, including genuine zeros and more than daily points', () => {
  const view = fixture('features/progress/PeriodCounts.tsx','PeriodCounts',{ counts: summary.counts });
  const labels = view.nodes().filter(node => node.props.accessible).map(node => node.props.accessibilityLabel);
  assert.deepEqual(labels,['体重记录 9 条','体脂记录 0 条','饮食记录 145 条','运动记录 3 条']);
});

test('active progress tab has apricot graph, while selection and all navigation callbacks remain intact', () => {
  const calls = [];
  const view = fixture('shared/navigation/BottomTabBar.tsx','BottomTabBar',{ selected: 'progress', bottomInset: 34, onSelect: value => calls.push(value) });
  const tabs = view.nodes().filter(node => node.type === 'Pressable');
  assert.equal(tabs[2].props.accessibilityState.selected,true);
  assert.equal(named(view,'AppIcon')[2].props.color,'#FBC098');
  tabs.forEach(node => node.props.onPress());
  assert.deepEqual(calls,['today','coach','progress','profile']);
});
