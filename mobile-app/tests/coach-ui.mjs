import assert from 'node:assert/strict';
import { test } from 'node:test';
import { fixture, tick, tree } from './support/native-fixture.mjs';

const instant = '2026-10-29T10:50:00Z';
const message = (id, extra = {}) => ({ id, turn_id: 't1', participant: 'user', role: 'user', kind: 'text', text: '你好', created_at: instant, ...extra });
const text = view => view.nodes().filter(node => node.type === 'Text').map(node => node.props.children).flat().join('');
const named = (view, name) => view.nodes().filter(node => typeof node.type === 'function' && node.type.name === name);
const native = { '../../components/PhotoGallery': { PhotoGallery: 'PhotoGallery' } };

test('chat is limited to user/coach messages; cards and system replies never become empty chat bubbles', () => {
  const view = fixture('features/coach-chat/conversation.ts', 'conversationItems');
  const { conversationItems } = view.load(new URL('../src/features/coach-chat/conversation.ts', import.meta.url).pathname);
  const result = conversationItems([
    message('user'), message('coach', { participant: 'coach', role: 'assistant' }),
    message('system', { participant: 'system_assistant' }), message('card', { participant: 'coach', kind: 'card' }),
    message('blank', { text: '  ' }), message('image', { text: null, kind: 'image', attachments: [{ asset_id: 'photo' }] }),
  ], 'Asia/Shanghai', new Date('2026-10-29T11:00:00Z'));
  assert.deepEqual(Array.from(result, row => row.message.id), ['user','coach','image']);
  assert.equal(result[0].timeLabel, '今天 18:50'); assert.equal(result[1].timeLabel, null);
});

test('timestamps use real message dates and user timezone, handle long gaps, old years and invalid values', () => {
  const view = fixture('features/coach-chat/conversation.ts', 'chatTimeLabel');
  const { chatTimeLabel, conversationItems } = view.load(new URL('../src/features/coach-chat/conversation.ts', import.meta.url).pathname);
  const now = new Date('2026-10-29T11:00:00Z');
  assert.equal(chatTimeLabel(instant, 'America/Los_Angeles', now), '今天 03:50');
  assert.equal(chatTimeLabel('2025-01-02T00:30:00Z', 'Asia/Shanghai', now), '2025年1月2日 08:30');
  assert.equal(chatTimeLabel('invalid', 'Asia/Shanghai', now), null);
  const result = conversationItems([message('one'), message('two', { created_at: '2026-10-29T11:00:00Z' })], 'Asia/Shanghai', now);
  assert.equal(result[1].timeLabel, '今天 19:00');
  const midnight = conversationItems([message('one', { created_at: '2026-10-28T15:59:00Z' }), message('two', { created_at: '2026-10-28T16:01:00Z' })], 'Asia/Shanghai', now);
  assert.equal(midnight[1].timeLabel, '今天 00:01');
});

test('user media uses the chat photo variant, white caption, no doctor avatar and preserves attachments', () => {
  const view = fixture('features/coach-chat/ChatBubble.tsx', 'ChatBubble', { message: message('photo', { attachments: [{ asset_id: 'one' },{ asset_id: 'two' }], text: '这是我的晚餐' }) }, native);
  assert.equal(named(view, 'CoachAvatar').length, 0);
  const gallery = view.find('PhotoGallery');
  assert.equal(gallery.props.variant, 'chat'); assert.equal(gallery.props.photos.length, 2);
  const caption = view.find('Text', props => props.children === '这是我的晚餐');
  assert.ok(caption.props.style.some(style => style?.color === '#FFFFFF'));
  assert.equal(caption.props.selectable, true);
});

test('doctor label stays outside the white bubble, uses the shared avatar and does not split the model reply', () => {
  const value = '第一段\n\n第二段';
  const view = fixture('features/coach-chat/ChatBubble.tsx', 'ChatBubble', { message: message('coach', { participant: 'coach', role: 'assistant', text: value }) }, native);
  assert.equal(named(view, 'CoachAvatar').length, 1);
  const label = view.find('Text', props => props.children === '医生教练');
  assert.ok(label);
  const bubble = view.find('View', props => Array.isArray(props.style) && props.style.some(style => style?.backgroundColor === '#FFFFFF'));
  assert.equal(tree(bubble).some(node => node.props.children === '医生教练'), false);
  assert.ok(tree(bubble).some(node => node.props.children === value));
});

test('pending and failed messages keep their delivery states, and defensive bubbles hide system/card data', () => {
  const pending = fixture('features/coach-chat/ChatBubble.tsx', 'ChatBubble', { message: message('pending', { pending: true }) }, native);
  assert.ok(text(pending).includes('发送中…'));
  pending.updateProps({ message: message('failed', { failed: true }) });
  assert.ok(text(pending).includes('发送失败'));
  pending.updateProps({ message: message('system', { participant: 'system_assistant' }) }); assert.equal(pending.render(), null);
  pending.updateProps({ message: message('card', { kind: 'card' }) }); assert.equal(pending.render(), null);
});

function composerFixture({ draft = '', send = async () => 'sent', assets = [], permission = true, imageError = false } = {}) {
  const alerts = [], calls = [], resizes = []; let consumed = 0, picked = 0;
  const view = fixture('features/coach-chat/useCoachComposer.ts', 'useCoachComposer', {}, {
    'react-native': { Alert: { alert: (...args) => alerts.push(args) } },
    'expo-image-picker': {
      requestCameraPermissionsAsync: async () => ({ granted: permission }),
      launchCameraAsync: async () => { picked++; return { canceled: !assets.length, assets }; },
      launchImageLibraryAsync: async options => { picked++; calls.push(options); return { canceled: !assets.length, assets }; },
    },
    'expo-image-manipulator': { SaveFormat: { JPEG: 'jpeg' }, ImageManipulator: { manipulate: uri => ({
      resize: size => resizes.push(size), renderAsync: async () => ({ saveAsync: async options => {
        if (imageError) throw new Error('decode failed');
        calls.push(options); return { uri: `converted:${uri}` };
      } }),
    }) } },
  });
  const { useCoachComposer } = view.load(new URL('../src/features/coach-chat/useCoachComposer.ts', import.meta.url).pathname);
  const consumeDraft = () => consumed++;
  const hook = view.mount({ type: () => useCoachComposer(draft, consumeDraft, (...args) => { calls.push(args); return send(...args); }), props: {} });
  return { ...hook, alerts, calls, resizes, consumed: () => consumed, picked: () => picked,
    async choose(label = '从相册选择') { hook.render().addPhoto(); alerts.at(-1)[2].find(item => item.text === label).onPress(); await tick(); },
  };
}

test('composer consumes incoming drafts once, rejects blank sends, sends text and blocks double taps', async () => {
  let resolveSend;
  const view = composerFixture({ draft: '从首页进入的文字', send: () => new Promise(resolve => { resolveSend = resolve; }) });
  view.render(); assert.equal(view.render().text, '从首页进入的文字');
  const first = view.render().send(); void view.render().send();
  assert.equal(view.calls.length, 1); assert.equal(view.render().sending, true); assert.equal(view.render().text, '');
  resolveSend('sent'); await first; assert.equal(view.render().sending, false);
  assert.equal(view.consumed(), 1);
  await view.render().send(); assert.equal(view.calls.length, 1);
});

test('send failure restores the payload without erasing a newer draft; queued/processing are not reported as failure', async () => {
  let rejectSend;
  const view = composerFixture({ send: () => new Promise((_resolve, reject) => { rejectSend = reject; }) });
  view.render().setText('原消息');
  const job = view.render().send(); view.render().setText('新草稿');
  rejectSend(new Error('network')); await job;
  assert.equal(view.render().text, '原消息\n新草稿');
  for (const result of ['queued','processing']) {
    const next = composerFixture({ draft: '晚餐', send: async () => result }); next.render(); await next.render().send();
    assert.equal(next.render().text, ''); assert.equal(next.alerts.length, result === 'queued' ? 1 : 0);
  }
});

test('photo selection preserves the 8-photo limit, resizes/compresses uploads and supports remove/photo-only send', async () => {
  const view = composerFixture({ assets: Array.from({ length: 10 }, (_, i) => ({ uri: `photo${i}`, width: i === 0 ? 2000 : 800 })) });
  await view.choose();
  assert.equal(view.render().images.length, 8); assert.equal(view.resizes.length, 1);
  assert.equal(view.resizes[0].width, 1600);
  assert.equal(view.calls.find(call => call.selectionLimit).selectionLimit, 8);
  view.render().addPhoto(); view.alerts.at(-1)[2].find(item => item.text === '从相册选择').onPress(); await tick();
  assert.equal(view.picked(), 1); assert.equal(view.alerts.at(-1)[0], '照片数量已达上限');
  view.render().removePhoto(1); assert.equal(view.render().images.length, 7);
  await view.render().send();
  const payload = view.calls.find(call => Array.isArray(call));
  assert.equal(payload[0], ''); assert.equal(payload[1].length, 7); assert.equal(payload[1][0].mimeType, 'image/jpeg');
});

test('camera denial, image cancellation and conversion errors do not send or discard a text draft', async () => {
  for (const options of [{ permission: false }, {}, { assets: [{ uri: 'bad', width: 500 }], imageError: true }]) {
    const view = composerFixture({ draft: '保留文字', ...options }); view.render();
    await view.choose(options.permission === false ? '拍照' : '从相册选择');
    assert.equal(view.render().text, '保留文字'); assert.equal(view.render().images.length, 0); assert.equal(view.render().picking, false);
    if (options.permission === false) assert.equal(view.alerts.at(-1)[0], '需要相机权限');
    if (options.imageError) assert.equal(view.alerts.at(-1)[0], '暂时无法添加照片');
  }
});

test('composer controls retain text/camera/send actions, multiline input and safe 44-point controls', () => {
  const sent = [], removed = [];
  const composer = { text: '', images: [], sending: false, picking: false, setText() {}, addPhoto() {}, send: () => sent.push(true) };
  const view = fixture('features/coach-chat/ChatComposer.tsx', 'ChatComposer', { composer, inputRef: { current: null } });
  assert.equal(view.find('TextInput').props.multiline, true);
  assert.equal(view.find('TextInput').props.placeholder, '吃了什么，或者想聊聊…');
  const button = () => view.find('Pressable', props => props.accessibilityLabel === '发送');
  assert.equal(button().props.disabled, true);
  composer.text = '你好'; assert.equal(button().props.disabled, false); button().props.onPress(); assert.equal(sent.length, 1);
  composer.sending = true; assert.equal(button().props.disabled, true);
  for (const label of ['添加照片','发送']) {
    const style = view.find('Pressable', props => props.accessibilityLabel === label).props.style;
    assert.ok((Array.isArray(style) ? style : [style]).some(item => item?.width >= 44 && item.height >= 44));
  }
  const strip = fixture('features/coach-chat/ChatComposer.tsx', 'SelectedPhotoStrip', { photos: [{ uri: 'one' },{ uri: 'two' }], onRemove: index => removed.push(index) });
  strip.find('Pressable', props => props.accessibilityLabel === '移除第 2 张照片').props.onPress(); assert.deepEqual(removed, [1]);
});

test('coach page keeps identity and notice/composer outside scrolling, handles empty/offline states without a fake conversation', () => {
  const listeners = {}, scrolls = []; let removed = 0;
  const data = { routine: { timezone: 'Asia/Shanghai' }, messages: [] };
  const state = { data, online: false, connection: { device: 'disconnected', backend: 'unknown' }, pending: [{}], notices: [], dismissNotice() {}, sendMessage() {} };
  const view = fixture('screens/CoachScreen.tsx', 'CoachScreen', { draft: '', consumeDraft() {} }, {
    '../context/AppContext': { useApp: () => state },
    '../features/coach-chat/useCoachComposer': { useCoachComposer: () => ({ images: [] }) },
    '../features/coach-chat/ChatComposer': { ChatComposer: 'ChatComposer', SelectedPhotoStrip: 'SelectedPhotoStrip' },
    '../features/system-notices/SystemNoticeBar': { SystemNoticeBar: 'SystemNoticeBar' },
    '../features/coach-chat/ChatBubble': { ChatBubble: 'ChatBubble' },
    $globals: { setTimeout: () => 1, clearTimeout() {} },
    $keyboard: (event, callback) => { listeners[event] = callback; return { remove() { removed++; } }; },
  });
  assert.equal(named(view, 'BrandHeader')[0].props.status, '离线模式');
  assert.ok(text(view).includes('1 条消息将在联网后自动发送'));
  const scroll = view.find('ScrollView');
  assert.equal(tree(scroll.props.children).some(node => node.type === 'SystemNoticeBar' || node.type === 'ChatComposer' || node.type.name === 'CoachIdentity'), false);
  assert.equal(view.find('KeyboardAvoidingView').props.behavior, 'padding');
  assert.equal(view.find('ChatBubble'), undefined); assert.ok(text(view).includes('慢慢来就好'));
  scroll.props.ref.current = { scrollToEnd: options => scrolls.push(options) };
  listeners.keyboardDidShow(); assert.equal(scrolls[0].animated, true);
  view.unmount(); assert.equal(removed, 1);
});

test('success notice is compact; failures are not drawn as success; questions remain replyable without a countdown', () => {
  const dismissed = [], replied = [];
  const base = { id: 'n1', text: '晚餐已记录', requiresResponse: false, expiresAt: Date.now() + 10000, outcome: 'success' };
  const view = fixture('features/system-notices/SystemNoticeBar.tsx', 'SystemNoticeBar', { notices: [base], onDismiss: id => dismissed.push(id), onReply: () => replied.push(true) });
  assert.equal(view.find('ScrollView'), undefined);
  assert.equal(view.find('Icon', props => props.name === 'checkmark').props.name, 'checkmark');
  assert.equal(named(view, 'NoticeCountdown').length, 1);
  view.find('Pressable', props => props.accessibilityLabel === '查看系统提示详情').props.onPress();
  assert.ok(view.find('ScrollView'));
  view.find('Pressable', props => props.accessibilityLabel === '关闭系统提示').props.onPress(); assert.deepEqual(dismissed, ['n1']);
  view.updateProps({ notices: [{ ...base, id: 'error', outcome: 'failed' }] });
  assert.ok(view.find('Icon', props => props.name === 'alert')); assert.equal(view.find('Icon', props => props.name === 'checkmark'), undefined);
  view.updateProps({ notices: [{ ...base, id: 'q', text: '这道菜叫什么？', requiresResponse: true, expiresAt: null }, base] });
  assert.equal(named(view, 'NoticeCountdown').length, 0); assert.ok(text(view).includes('这道菜叫什么'));
  view.find('Pressable', props => tree(props.children).some(node => node.props.children === '回复这条提示')).props.onPress(); assert.equal(replied.length, 1);
});

test('notice countdown reads its real expiry and cleans up its clock; it does not own dismissal', () => {
  let now = 1000, timer, cleared = 0;
  class Clock extends Date { static now() { return now; } }
  const view = fixture('features/system-notices/SystemNoticeBar.tsx', 'NoticeCountdown', { expiresAt: 11000 }, {
    $globals: { Date: Clock, setInterval: callback => { timer = callback; return 42; }, clearInterval: id => { assert.equal(id, 42); cleared++; } },
  });
  assert.equal(text(view), '10s'); now = 4200; timer(); assert.equal(text(view), '7s');
  now = 12000; timer(); assert.equal(text(view), '0s'); view.unmount(); assert.equal(cleared, 1);
});
