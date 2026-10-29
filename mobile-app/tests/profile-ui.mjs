import assert from 'node:assert/strict';
import { test } from 'node:test';
import { fixture, tick, tree } from './support/native-fixture.mjs';

// Hook/event tests only; native layout is checked separately in the simulator.
const profile = { age_band: '30_39', height_cm: 170, current_weight_kg: 79, weight_measured_on: '2026-10-29', goal_type: 'lose_weight', target_weight_kg: 73,
  target_date: '2099-12-28', current_body_fat_percent: null, target_body_fat_percent: null, exercise_frequency: 'weekly_1_2' };
const routine = { timezone: 'Asia/Shanghai', weight_reminder_time: '08:15', meal_reminder_time: null, daily_review_time: '21:30' };
const user = { id: 'u1', nickname: '小林', identity_hint: '手机号已验证' };
const text = view => view.nodes().filter(node => node.type === 'Text').flatMap(node => node.props.children).filter(value => typeof value !== 'object').join('');
const named = (view, name) => view.nodes().filter(node => typeof node.type === 'function' && node.type.name === name);
const pending = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const formModule = fixture('features/health-profile/form.ts', 'profileForm', null).load(new URL('../src/features/health-profile/form.ts', import.meta.url).pathname);

test('profile landing integrates identity inside health card, without a separate 我的 heading', async () => {
  const calls = [], app = { data: { user, routine, coach_profile: { profile } }, online: true, pending: [], loading: false,
    refresh: () => calls.push('refresh'), getWeComBinding: async () => null, createWeComBinding: async () => ({}) };
  const view = fixture('screens/ProfileScreen.tsx', 'ProfileScreen', { openPage: page => calls.push(page), openCoachProfile: () => calls.push('edit') }, {
    '../context/AppContext': { useApp: () => app },
  });
  view.render(); await tick();
  assert.equal(text(view).includes('我的'), false);
  assert.equal(tree(view.find('ScrollView')).some(node => node.type.name === 'BrandHeader'), false);
  assert.equal(named(view, 'ProfileHealthCard').length, 1);
  assert.equal(named(view, 'ProfileMenu')[0].props.reminderCount, 2);
  assert.equal(named(view, 'ProfileMenu')[0].props.connectionLabel, '未连接');
  named(view, 'ProfileHealthCard')[0].props.onEdit(); named(view, 'ProfileMenu')[0].props.onOpen('memory');
  assert.deepEqual(calls, ['edit', 'memory']);
  app.online = false; app.pending = [{}];
  assert.equal(named(view, 'BrandHeader')[0].props.status, '1 条待发送');
  view.find('ScrollView').props.refreshControl.props.onRefresh();
  assert.equal(calls.at(-1), 'refresh'); view.unmount();
});

test('health card uses real values and one edit entry; missing profile does not fabricate weights', () => {
  const calls = [], view = fixture('features/user-profile/ProfileHealthCard.tsx', 'ProfileHealthCard', { user, profile, onEdit: () => calls.push('edit') });
  assert.ok(text(view).includes('小林')); assert.ok(text(view).includes('健康档案 · 手机号已验证'));
  assert.deepEqual(named(view, 'Value').map(node => view.mount(node).render().props.accessibilityLabel), ['当前体重 79 kg', '目标体重 73 kg']);
  view.find('Pressable', props => props.accessibilityLabel === '编辑健康档案').props.onPress(); assert.deepEqual(calls, ['edit']);
  view.updateProps({ profile: null, user: { ...user, nickname: null, identity_hint: null } });
  assert.ok(text(view).includes('健康档案 · 已登录')); assert.ok(view.find('Pressable', props => props.accessibilityLabel === '填写健康档案'));
  const value = view.mount(named(view, 'Value')[0]); assert.ok(text(value).includes('—')); assert.equal(text(value).includes('79'), false);
});

test('all four settings menu entries navigate to actual details', () => {
  const calls = [], view = fixture('features/user-profile/ProfileMenu.tsx', 'ProfileMenu', { reminderCount: 0, connectionLabel: '读取失败', onOpen: page => calls.push(page) });
  const rows = named(view, 'Row'); rows.forEach(node => view.mount(node).find('Pressable').props.onPress());
  assert.deepEqual(calls, ['memory', 'reminders', 'wecom', 'account']);
  assert.equal(rows[1].props.value, '0 项开启'); assert.equal(rows[2].props.value, '读取失败');
});

test('profile form preserves backend fields, empty optional values and one-decimal normalization', () => {
  const form = formModule.profileForm(profile, '小林'), result = formModule.validateHealthForm(form, '2026-10-29');
  assert.equal(result.error, null); assert.deepEqual(JSON.parse(JSON.stringify(result.payload)), profile);
  assert.equal(formModule.sanitizeNumber('79,45kg'), '79.4'); assert.equal(formModule.sanitizeNumber('1.2.3'), '1.2');
  assert.equal(formModule.ageOptions.length, 9); assert.equal(formModule.exerciseOptions.length, 4);
  assert.equal(formModule.healthSignature(form), formModule.healthSignature({ ...form, nickname: '另一个称呼' }));
});

test('validation keeps numeric limits, real calendar dates, loss goal and optional-fat constraints', () => {
  const original = formModule.profileForm(profile, '小林');
  for (const [key, value] of [['ageBand', null], ['height', '49'], ['height', '251'], ['currentWeight', '9'], ['targetWeight', '501'], ['currentWeight', '79.22'],
    ['weightDate', '2026-02-30'], ['weightDate', '2099-01-01'], ['targetDate', '2026-10-29'], ['targetDate', '2099-02-30'], ['targetWeight', '79'], ['currentBodyFat', '0'], ['targetBodyFat', '76']]) {
    assert.equal(formModule.validateHealthForm({ ...original, [key]: value }, '2026-10-29').payload, null, `${key}=${value}`);
  }
  assert.equal(formModule.validateHealthForm({ ...original, goalType: 'maintain_weight', targetWeight: '79' }, '2026-10-29').error, null);
  assert.equal(formModule.validateHealthForm({ ...original, currentBodyFat: '25.5', targetBodyFat: '21' }, '2026-10-29').payload.current_body_fat_percent, 25.5);
});

function editor(overrides = {}) {
  const calls = [], alerts = [];
  const props = { profile, nickname: user.nickname, intent: 'edit', updateProfile: async value => { calls.push(['name', value]); },
    saveCoachProfile: async input => { calls.push(['health', input]); return { profile: input, coach_enabled: true }; }, onClose: () => calls.push('close'), onReady: () => calls.push('ready'), ...overrides };
  const view = fixture('features/health-profile/useProfileEditor.ts', 'useProfileEditor', props, { $alert: (...args) => alerts.push(args), $globals: { Error } });
  return { view, calls, alerts };
}

test('untouched editor closes immediately; a changed draft requires explicit discard', () => {
  const { view, calls, alerts } = editor();
  view.render().requestClose(); assert.deepEqual(calls, ['close']);
  view.render().change('nickname', '新称呼'); view.render().requestClose();
  assert.equal(alerts.length, 1); assert.deepEqual(calls, ['close']);
  alerts[0][2][1].onPress(); assert.deepEqual(calls, ['close', 'close']);
});

test('nickname-only save bypasses expired health goals and does not resend health PUT', async () => {
  const { view, calls } = editor({ profile: { ...profile, target_date: '2000-01-01' } });
  view.render().change('nickname', '  小二  '); await view.render().save();
  assert.deepEqual(calls, [['name', '小二'], 'close']);
});

test('nickname can be edited before a health profile exists without bypassing the coach gate', async () => {
  const normal = editor({ profile: null }); normal.view.render().change('nickname', '小二'); await normal.view.render().save();
  assert.deepEqual(normal.calls, [['name', '小二'], 'close']);
  const gate = editor({ profile: null, intent: 'coach' }); gate.view.render().change('nickname', '小二'); await gate.view.render().save();
  assert.deepEqual(gate.calls, []); assert.ok(gate.view.render().error.includes('必填'));
});

test('health and identity changes save to their proper APIs, then close the editor', async () => {
  const { view, calls } = editor(); view.render().change('targetWeight', '72'); view.render().change('nickname', '小二'); await view.render().save();
  assert.equal(calls[0][0], 'health'); assert.equal(calls[0][1].target_weight_kg, 72);
  assert.deepEqual(calls[1], ['name', '小二']); assert.equal(calls[2], 'close');
});

test('partial save acknowledges accepted health data; retry submits only the failed nickname', async () => {
  let attempts = 0;
  const { view, calls } = editor({ updateProfile: async name => { calls.push(['name', name]); if (!attempts++) throw new Error('网络错误'); } });
  view.render().change('height', '171'); view.render().change('nickname', '小二'); await view.render().save();
  assert.match(view.render().error, /健康档案已保存，称呼未保存.*网络错误/); assert.equal(calls.includes('close'), false);
  await view.render().save();
  assert.equal(calls.filter(value => Array.isArray(value) && value[0] === 'health').length, 1);
  assert.equal(calls.filter(value => Array.isArray(value) && value[0] === 'name').length, 2); assert.equal(calls.at(-1), 'close');
});

test('failed or unconfirmed health saves keep the draft and never open the coach', async () => {
  for (const save of [async () => { throw new Error('保存失败'); }, async () => ({ profile: null, coach_enabled: false })]) {
    const { view, calls } = editor({ intent: 'coach', saveCoachProfile: save }); view.render().change('height', '171'); await view.render().save();
    assert.equal(view.render().form.height, '171'); assert.ok(view.render().error); assert.deepEqual(calls, []);
  }
});

test('background refresh cannot overwrite an open editor draft', () => {
  const { view } = editor(); view.render().change('nickname', '草稿'); view.render().change('height', '171');
  view.updateProps({ profile: { ...profile, height_cm: 180 }, nickname: '服务器称呼' });
  assert.equal(view.render().form.nickname, '草稿'); assert.equal(view.render().form.height, '171');
});

test('save lock rejects double taps, edits and back navigation during an in-flight write', async () => {
  const deferred = pending(); let count = 0;
  const { view, calls, alerts } = editor({ saveCoachProfile: async input => { count++; await deferred.promise; return { profile: input, coach_enabled: true }; } });
  view.render().change('height', '171'); const first = view.render().save(); const second = view.render().save();
  view.render().change('height', '180'); view.render().requestClose();
  assert.equal(view.render().saving, true); assert.equal(view.render().form.height, '171'); assert.equal(count, 1); assert.equal(alerts.length, 0);
  deferred.resolve(); await Promise.all([first, second]); assert.deepEqual(calls, ['close']);
});

test('unchanged coach profile can enter the coach without redundant writes', async () => {
  const { view, calls } = editor({ intent: 'coach' }); await view.render().save(); assert.deepEqual(calls, ['ready']);
});

test('health editor fields use selectable choices, compact date limits and collapsed optional inputs', () => {
  const changes = [], view = fixture('features/health-profile/HealthProfileFields.tsx', 'HealthProfileFields', { form: formModule.profileForm(profile, '小林'), saving: false, change: (...value) => changes.push(value) });
  assert.equal(named(view, 'NumberField').length, 3);
  assert.equal(named(view, 'ChoicePicker')[0].props.options.length, 9);
  assert.equal(named(view, 'DateField').every(node => node.props.compact), true);
  assert.ok(named(view, 'DateField')[0].props.maximumDate); assert.ok(named(view, 'DateField')[1].props.minimumDate);
  view.find('Pressable', props => props.accessibilityLabel === '改善习惯').props.onPress(); assert.deepEqual(changes, [['goalType', 'improve_habits']]);
  view.find('Pressable', props => props.accessibilityLabel === '补充信息').props.onPress();
  assert.equal(named(view, 'NumberField').length, 5); assert.equal(named(view, 'ChoicePicker')[1].props.allowClear, true);
  view.updateProps({ saving: true }); assert.equal(view.find('TextInput').props.editable, false);
  assert.equal(named(view, 'DateField').every(node => node.props.disabled), true);
});

test('editor page uses bottom actions, centered heading and preserves both edit and coach intents', () => {
  const app = { data: { user, coach_profile: { profile } }, saveCoachProfile: async () => ({}), updateProfile: async () => {} };
  const view = fixture('screens/CoachProfileScreen.tsx', 'CoachProfileScreen', { intent: 'edit', onClose() {}, onReady() {} }, { '../context/AppContext': { useApp: () => app } });
  assert.equal(view.render().props.title, '编辑健康档案'); assert.equal(view.render().props.centeredTitle, true);
  assert.deepEqual(tree(view.render().props.footer).filter(node => node.type.name === 'SettingsAction').map(node => node.props.title), ['取消', '保存修改']);
  view.updateProps({ intent: 'coach' }); assert.equal(view.render().props.title, '健康档案');
  assert.equal(tree(view.render().props.footer).find(node => node.props.title === '保存并进入教练').props.busy, false);
});

test('detail page footer is outside scrolling content and hardware back respects the write lock', () => {
  const calls = []; let handler; const view = fixture('shared/ui/DetailPage.tsx', 'DetailPage', { title: '编辑', onClose: () => calls.push('close'), children: 'form', footer: 'actions' }, {
    $backHandler: (_event, callback) => { handler = callback; return { remove: () => calls.push('remove') }; },
  });
  view.render(); assert.equal(tree(view.find('ScrollView')).some(node => node.props.children === 'actions'), false);
  assert.equal(handler(), true); assert.equal(calls[0], 'close');
  view.updateProps({ busy: true }); handler(); assert.deepEqual(calls, ['close', 'remove']);
  assert.equal(view.find('Pressable').props.disabled, true); view.unmount(); assert.equal(calls.at(-1), 'remove');
});

test('choice picker dismisses keyboard, supports native dismissal, selection and optional clearing', () => {
  const calls = [], view = fixture('shared/ui/ChoicePicker.tsx', 'ChoicePicker', { label: '年龄段', options: formModule.ageOptions, value: '30_39', onChange: value => calls.push(value), allowClear: true }, { $dismissKeyboard: () => calls.push('keyboard') });
  view.find('Pressable', props => props.accessibilityLabel === '年龄段').props.onPress(); assert.equal(calls[0], 'keyboard');
  assert.equal(view.nodes().filter(node => node.props.accessibilityRole === 'radio').length, 9);
  view.find('Modal').props.onRequestClose(); assert.equal(view.find('Modal'), undefined);
  view.find('Pressable').props.onPress();
  view.find('Pressable', props => props.accessibilityRole === 'radio' && tree(props.children).some(node => node.props.children === '40–49 岁')).props.onPress();
  assert.equal(calls.at(-1), '40_49'); assert.equal(view.find('Modal'), undefined);
  view.find('Pressable').props.onPress(); view.find('Pressable', props => tree(props.children).some(node => node.props.children === '暂不填写')).props.onPress(); assert.equal(calls.at(-1), null);
  view.updateProps({ disabled: true }); assert.equal(view.find('Pressable').props.disabled, true);
});

const memories = [
  { id: 'g', kind: 'goal', key: 'goal.target_weight', value: { grams: 73000, target_date: '2026-12-28' }, stale: false },
  { id: 'f', kind: 'profile', key: 'food.preference', value: { item: '清淡口味', stance: 'like' }, stale: false },
  { id: 'e', kind: 'profile', key: 'exercise.preference', value: { activity: '饭后散步', stance: 'like' }, stale: false },
  { id: 'c', kind: 'constraint', key: 'constraint.dietary', value: { statement: '暂时避免辛辣食物' }, stale: true },
];
const memoryModule = fixture('features/coach-memory/presentation.ts', 'memoryGroup', memories[0]).load(new URL('../src/features/coach-memory/presentation.ts', import.meta.url).pathname);

test('memory categories use real backend profile kinds and retain unfamiliar facts', () => {
  assert.deepEqual(memories.map(memoryModule.memoryGroup), ['goal', 'preference', 'preference', 'constraint']);
  const unknown = { kind: 'profile', key: 'new.fact', value: { statement: '新的信息' } };
  assert.equal(memoryModule.memoryGroup(unknown), 'other'); assert.equal(memoryModule.memoryPresentation(unknown).label, '其他信息');
  assert.equal(memoryModule.memoryPresentation(unknown).value, '新的信息');
  assert.equal(memoryModule.memoryPresentation(memories[0]).value, '73 kg');
  assert.equal(memoryModule.memoryPresentation(memories[0]).detail, '希望在 2026/12/28 前达到');
  assert.equal(memoryModule.memoryPresentation(memories[1]).value, '喜欢清淡口味');
});

function memoryScreen(items = memories) {
  const calls = [], app = { data: { memories: items } };
  const view = fixture('features/coach-memory/CoachMemoryScreen.tsx', 'CoachMemoryScreen', { onClose: () => calls.push('close'), openCoach: value => calls.push(value) }, { '../../context/AppContext': { useApp: () => app } });
  return { view, calls, app };
}

test('memory details use actual counts, category filters and stale status without adding CRUD', () => {
  const { view, calls } = memoryScreen();
  assert.equal(view.render().props.trailing.props.children.join(''), '4 条');
  assert.equal(named(view, 'MemoryRow').length, 4);
  assert.equal(named(view, 'MemoryRow').map(node => view.mount(node)).filter(row => text(row).includes('待确认')).length, 1);
  view.find('Pressable', props => props.accessibilityLabel === '习惯偏好').props.onPress();
  assert.deepEqual(named(view, 'MemoryRow').map(node => node.props.memory.id), ['f', 'e']);
  assert.equal(text(view).includes('你的目标'), false);
  const footer = tree(view.render().props.footer).find(node => node.type.name === 'SettingsAction'); footer.props.onPress();
  assert.deepEqual(calls, ['我想更新一项个人资料：']); assert.equal(named(view, 'MemoryRow').some(node => node.props.onDelete), false);
});

test('memory empty states and unknown category preserve facts without fake example content', () => {
  const { view } = memoryScreen([]); assert.ok(text(view).includes('还没有长期记忆')); assert.equal(text(view).includes('73'), false);
  const unknown = memoryScreen([{ id: 'x', kind: 'profile', key: 'new.fact', value: { statement: '新的信息' } }]).view;
  assert.ok(unknown.find('Pressable', props => props.accessibilityLabel === '其他')); assert.equal(named(unknown, 'MemoryRow').length, 1);
  unknown.find('Pressable', props => props.accessibilityLabel === '目标').props.onPress(); assert.ok(text(unknown).includes('这一类还没有记忆'));
});

function reminder(overrides = {}) {
  const calls = [], alerts = [], props = { initial: routine, update: async value => { calls.push(value); return true; }, onClose: () => calls.push('close'), ...overrides };
  const view = fixture('features/user-profile/useReminderForm.ts', 'useReminderForm', props, { $alert: (...args) => alerts.push(args), $globals: { Error } });
  // Hook has positional arguments; bridge it through a tiny fixture component.
  const hook = view.load(new URL('../src/features/user-profile/useReminderForm.ts', import.meta.url).pathname).useReminderForm;
  const mounted = view.mount({ type: input => hook(input.initial, input.update, input.onClose), props });
  return { view: mounted, calls, alerts };
}

test('reminder toggles retain a custom time and use defaults only for never-configured entries', () => {
  const { view } = reminder(); view.render().toggle('weight_reminder_time', false); view.render().toggle('weight_reminder_time', true);
  assert.equal(view.render().routine.weight_reminder_time, '08:15');
  view.render().toggle('meal_reminder_time', true); assert.equal(view.render().routine.meal_reminder_time, '12:30');
  view.render().change('meal_reminder_time', '13:05'); view.render().toggle('meal_reminder_time', false); view.render().toggle('meal_reminder_time', true);
  assert.equal(view.render().routine.meal_reminder_time, '13:05');
});

test('reminder time validation prevents writes, while permission denial acknowledges saved settings', async () => {
  const { view, calls } = reminder({ update: async value => { calls.push(value); return false; } });
  view.render().change('weight_reminder_time', '25:00'); await view.render().save(); assert.ok(view.render().error.includes('24 小时')); assert.equal(calls.length, 0);
  view.render().change('weight_reminder_time', '09:00'); await view.render().save(); assert.ok(view.render().notice.includes('设置已保存')); assert.ok(view.render().notice.includes('权限未开启'));
  view.render().close(); assert.equal(calls.at(-1), 'close');
});

test('reminder drafts survive refresh, require discard and lock writes while saving', async () => {
  const deferred = pending(), writes = [], { view, alerts } = reminder({ update: async value => { writes.push(value); await deferred.promise; return true; } });
  view.render().change('weight_reminder_time', '09:00'); view.updateProps({ initial: { ...routine, weight_reminder_time: '10:00' } });
  assert.equal(view.render().routine.weight_reminder_time, '09:00'); view.render().close(); assert.equal(alerts.length, 1);
  const first = view.render().save(); await view.render().save(); view.render().change('weight_reminder_time', '11:00');
  assert.equal(writes.length, 1); assert.equal(view.render().routine.weight_reminder_time, '09:00');
  deferred.resolve(); await first; assert.ok(view.render().notice.includes('提醒已保存'));
});

test('WeChat label distinguishes errors, pending expiry, conflict and confirmed binding', () => {
  const module = fixture('features/user-profile/useWeComBinding.ts', 'bindingLabel').load(new URL('../src/features/user-profile/useWeComBinding.ts', import.meta.url).pathname);
  assert.equal(module.bindingLabel(null, true, null), '读取中…'); assert.equal(module.bindingLabel(null, false, 'error'), '读取失败');
  assert.equal(module.bindingLabel(null, false, null), '未连接'); assert.equal(module.bindingLabel({ status: 'claimed' }, false, null), '已连接');
  assert.equal(module.bindingLabel({ status: 'conflict' }, false, null), '需要处理');
  assert.equal(module.bindingLabel({ status: 'pending', expires_at: '2000-01-01' }, false, null), '未连接');
  assert.equal(module.bindingLabel({ status: 'pending', expires_at: '2099-01-01' }, false, null), '待连接');
});

test('WeChat requests do not overlap and ignore old responses after ports change or unmount', async () => {
  const first = pending(), second = pending(), writes = [];
  const initial = fixture('features/user-profile/useWeComBinding.ts', 'useWeComBinding');
  const hook = initial.load(new URL('../src/features/user-profile/useWeComBinding.ts', import.meta.url).pathname).useWeComBinding;
  const props = { get: () => first.promise, create: async () => { writes.push('create'); return { status: 'pending' }; } };
  const view = initial.mount({ type: input => hook(input.get, input.create), props }); view.render(); await view.render().request('create'); assert.equal(writes.length, 0);
  view.updateProps({ get: () => second.promise }); second.resolve({ status: 'claimed' }); await tick(); first.resolve(null); await tick();
  assert.equal(view.render().binding.status, 'claimed'); assert.equal(view.render().busy, false);
  const deferred = pending(); view.updateProps({ get: () => deferred.promise }); view.unmount(); deferred.resolve({ status: 'conflict' }); await tick();
  // Last committed binding remains unchanged; no post-unmount response is applied.
  assert.equal(view.render().binding.status, 'claimed'); view.unmount();
});

test('WeChat failed reads can retry without masquerading as disconnected', async () => {
  const initial = fixture('features/user-profile/useWeComBinding.ts', 'useWeComBinding', {}, { $globals: { Error } });
  const hook = initial.load(new URL('../src/features/user-profile/useWeComBinding.ts', import.meta.url).pathname).useWeComBinding;
  let fail = true; const props = { get: async () => { if (fail) throw new Error('读取失败'); return { status: 'claimed' }; }, create: async () => ({}) };
  const view = initial.mount({ type: input => hook(input.get, input.create), props }); view.render(); await tick(); assert.equal(view.render().error, '读取失败');
  fail = false; await view.render().request(); assert.equal(view.render().error, null); assert.equal(view.render().binding.status, 'claimed'); view.unmount();
});

test('account deletion remains doubly confirmed; cancel and initial render cause no external mutation', async () => {
  const calls = [], alerts = [], view = fixture('features/user-profile/AccountScreen.tsx', 'AccountScreen', { onClose() {} }, {
    '../../context/AppContext': { useApp: () => ({ logout: async () => calls.push('logout'), deleteAccount: async () => calls.push('delete') }) }, $alert: (...args) => alerts.push(args),
  });
  assert.equal(calls.length, 0); named(view, 'SettingsAction').find(node => node.props.title === '永久删除账号').props.onPress();
  assert.equal(calls.length, 0); assert.equal(alerts.length, 1); alerts[0][2][1].onPress(); assert.equal(calls.length, 0); assert.equal(alerts.length, 2);
  alerts[1][2][1].onPress(); await tick(); assert.deepEqual(calls, ['delete']);
});

test('account API failures are visible and retryable without accidentally navigating away', async () => {
  const alerts = [], view = fixture('features/user-profile/AccountScreen.tsx', 'AccountScreen', { onClose() { assert.fail('unexpected navigation'); } }, {
    '../../context/AppContext': { useApp: () => ({ logout: async () => { throw new Error('网络错误'); }, deleteAccount: async () => {} }) }, $alert: (...args) => alerts.push(args), $globals: { Error },
  });
  named(view, 'SettingsAction').find(node => node.props.title === '退出登录').props.onPress(); alerts[0][2][1].onPress(); await tick();
  assert.ok(text(view).includes('网络错误')); assert.equal(view.render().props.busy, false);
});

test('profile active tab follows indigo/apricot styling without changing navigation', () => {
  const calls = [], view = fixture('shared/navigation/BottomTabBar.tsx', 'BottomTabBar', { selected: 'profile', bottomInset: 34, onSelect: tab => calls.push(tab) });
  assert.equal(named(view, 'AppIcon')[3].props.color, '#FBC098'); view.nodes().filter(node => node.type === 'Pressable')[3].props.onPress(); assert.deepEqual(calls, ['profile']);
});

function contextFixture(apiOverrides = {}) {
  const dashboard = { user, routine, coach_profile: { profile }, messages: [], memories: [], today: { routine } };
  const calls = [], api = { onAuthChanged: () => () => {}, restore: async () => ({ access_token: 'test' }), authOptions: async () => ({}),
    dashboard: async () => { calls.push('dashboard'); return dashboard; }, logout: async () => {}, saveCoachProfile: async input => { calls.push('save'); return { profile: input, coach_enabled: true }; },
    updateProfile: async nickname => ({ ...user, nickname }), ...apiOverrides };
  const view = fixture('context/AppContext.tsx', 'AppProvider', { children: 'app' }, {
    '../lib/api': { mobileApi: api, ApiError: class extends Error {} },
    '../shared/network/useConnection': { useConnection: () => ({ device: 'connected', backend: 'reachable' }) },
    '../features/coach-chat/useChatDelivery': { useChatDelivery: () => ({ pending: [], sendMessage() {} }) },
    '../lib/offlineQueue': { listPendingChats: async () => [] }, '../lib/notifications': { syncRoutineNotifications: async () => true },
    '../features/system-notices/useSystemNotices': { useSystemNotices: () => ({ notices: [], dismissNotice() {} }) },
    '@react-native-community/netinfo': { __esModule: true, default: { addEventListener: () => () => {} } },
    'expo-crypto': {}, 'expo-file-system': {},
  });
  return { view, calls, api };
}

test('committed profile PUT updates context directly; dashboard refresh failure cannot turn it into failed save', async () => {
  const { view, calls, api } = contextFixture(); view.render(); await tick();
  assert.equal(calls.length, 1); api.dashboard = async () => { throw new Error('dashboard unavailable'); };
  const saved = await view.render().props.value.saveCoachProfile({ ...profile, height_cm: 171 });
  assert.equal(saved.profile.height_cm, 171); assert.equal(view.render().props.value.data.coach_profile.profile.height_cm, 171);
  assert.deepEqual(calls, ['dashboard', 'save']); view.unmount();
});

test('late identity/profile responses cannot revive signed-out data', async () => {
  const health = pending(), name = pending(), { view } = contextFixture({ saveCoachProfile: () => health.promise, updateProfile: () => name.promise });
  view.render(); await tick(); const app = view.render().props.value;
  const healthWrite = app.saveCoachProfile(profile), nameWrite = app.updateProfile('新称呼');
  await app.logout(); assert.equal(view.render().props.value.data, null);
  health.resolve({ profile, coach_enabled: true }); name.resolve({ ...user, nickname: '新称呼' }); await Promise.all([healthWrite, nameWrite]);
  assert.equal(view.render().props.value.data, null); view.unmount();
});

test('a late dashboard refresh cannot revive signed-out data', async () => {
  const job = pending(), { view, api } = contextFixture(); view.render(); await tick();
  const original = view.render().props.value.data;
  api.dashboard = () => job.promise;
  const refreshing = view.render().props.value.refresh();
  await view.render().props.value.logout();
  job.resolve(original); await refreshing;
  assert.equal(view.render().props.value.data, null); view.unmount();
});

test('WeChat screen exposes only valid pending codes, checking instead of overwriting conflicts', () => {
  const calls = [], state = { busy: false, error: null, binding: { status: 'pending', code: '123456', expires_at: '2099-01-01T12:00:00Z' }, request: action => calls.push(action) };
  const view = fixture('features/user-profile/WeComScreen.tsx', 'WeComScreen', { onClose() {} }, {
    '../../context/AppContext': { useApp: () => ({}) }, './useWeComBinding': { useWeComBinding: () => state },
  });
  assert.ok(text(view).includes('SG-123456')); named(view, 'SettingsAction')[0].props.onPress(); assert.deepEqual(calls, ['check']);
  state.binding.expires_at = '2000-01-01'; assert.equal(text(view).includes('SG-123456'), false); assert.equal(named(view, 'SettingsAction')[0].props.title, '生成绑定码');
  state.binding = { status: 'conflict' }; named(view, 'SettingsAction')[0].props.onPress(); assert.equal(calls.at(-1), 'check'); assert.ok(text(view).includes('没有擅自覆盖'));
  state.binding = { status: 'claimed' }; assert.equal(named(view, 'SettingsAction')[0].props.title, '检查连接状态');
});
