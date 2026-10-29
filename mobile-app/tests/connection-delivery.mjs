import assert from 'node:assert/strict';
import { test } from 'node:test';
import { fixture, tick } from './support/native-fixture.mjs';

const monitorModule = fixture('shared/network/connection.ts', 'connectionLabel', {}).load(new URL('../src/shared/network/connection.ts', import.meta.url).pathname);
const deferred = () => { let resolve; const promise = new Promise(yes => { resolve = yes; }); return { promise, resolve }; };
const settled = async () => { await tick(); await tick(); };

test('physical connectivity, backend reachability and processing timeouts are independent', () => {
  const monitor = new monitorModule.ConnectionMonitor(); monitor.device(true); monitor.response(200);
  monitor.failure('request_timeout'); assert.equal(monitor.snapshot().backend, 'reachable');
  monitor.response(401); assert.equal(monitorModule.connectionLabel(monitor.snapshot()), '教练在线');
  monitor.response(500); assert.equal(monitorModule.connectionLabel(monitor.snapshot()), '服务暂不可用');
  monitor.failure('network_unavailable'); assert.equal(monitorModule.connectionLabel(monitor.snapshot()), '暂时连不上服务');
  assert.equal(monitor.snapshot().device, 'connected');
  monitor.device(false); assert.equal(monitorModule.connectionLabel(monitor.snapshot()), '离线模式');
});

test('a stale failed probe cannot overwrite a newer successful API response', async () => {
  const monitor = new monitorModule.ConnectionMonitor(), job = deferred(); monitor.device(true);
  const pending = monitor.probe(() => job.promise); monitor.response(200); job.resolve(false); await pending;
  assert.equal(monitor.snapshot().backend, 'reachable');
});

test('a stale successful probe cannot hide a newer device disconnection', async () => {
  const monitor = new monitorModule.ConnectionMonitor(), job = deferred(); monitor.device(true);
  const pending = monitor.probe(() => job.promise); monitor.device(false); job.resolve(true); await pending;
  assert.equal(monitor.snapshot().backend, 'unknown'); assert.equal(monitor.snapshot().device, 'disconnected');
});

test('probe failures recover without declaring the device disconnected; listeners clean up', async () => {
  const monitor = new monitorModule.ConnectionMonitor(), changes = [], remove = monitor.subscribe(state => changes.push(state.backend));
  monitor.device(true); await monitor.probe(async () => { throw new Error('timeout'); });
  assert.equal(monitor.snapshot().backend, 'unavailable'); await monitor.probe(async () => true);
  assert.equal(monitor.snapshot().backend, 'reachable'); remove(); monitor.response(503);
  assert.deepEqual(changes, ['unknown', 'unavailable', 'reachable']);
});

test('NetInfo Google reachability is disabled and a false internet flag never gates API connectivity', async () => {
  let notify, config; const calls = [], timers = new Map();
  const view = fixture('shared/network/useConnection.ts', 'useConnection', {}, {
    '../../lib/api': { API_BASE_URL: 'https://enceladus.online' },
    '@react-native-community/netinfo': { __esModule: true, default: { configure: value => { config = value; }, addEventListener: fn => { notify = fn; return () => calls.push('unsubscribe'); }, fetch: async () => ({ isConnected: true }) } },
    $globals: { AbortController, fetch: async (url, init) => { calls.push({ url, init }); return { status: 200, json: async () => ({ status: 'ok' }) }; },
      setTimeout: fn => { timers.set(1, fn); return 1; }, clearTimeout: id => timers.delete(id), setInterval: () => 2, clearInterval() {} },
  });
  view.render(); await settled(); notify({ isConnected: true, isInternetReachable: false }); await settled();
  const state = view.render(); assert.equal(config.reachabilityShouldRun(), false);
  assert.equal(state.device, 'connected'); assert.equal(state.backend, 'reachable');
  assert.equal(calls[0].url, 'https://enceladus.online/health/live'); assert.equal(calls[0].init.method, 'GET');
  assert.equal(calls[0].init.cache, 'no-store'); assert.equal(timers.size, 0); view.unmount(); assert.equal(calls.at(-1), 'unsubscribe');
});

test('health probe requires both HTTP 200 and the expected body, never a proxy HTML success page', async () => {
  for (const [status, body, expected] of [[200, { status: 'ok' }, true], [200, {}, false], [503, { status: 'ok' }, false]]) {
    const view = fixture('shared/network/useConnection.ts', 'checkBackend', {}, {
      '../../lib/api': { API_BASE_URL: 'https://enceladus.online' }, '@react-native-community/netinfo': {},
      $globals: { AbortController, fetch: async () => ({ status, json: async () => body }) },
    });
    assert.equal(await view.render(), expected);
  }
});

class ApiError extends Error { constructor(status, code) { super(code); this.status = status; this.code = code; } }
function delivery({ connected = true, send = async () => ({ status: 'succeeded', request_id: 'r1', messages: [] }), initialQueue = [] } = {}) {
  const queue = [...initialQueue], posts = [], errors = [], timers = new Map(), enqueued = [];
  let data = { user: { id: 'u1' }, messages: [] }, foreground, refreshed = 0, serial = 0;
  const view = fixture('features/coach-chat/useChatDelivery.ts', 'useChatDelivery', { userId: 'u1', connected,
    setData: update => { data = update(data); }, onError: value => errors.push(value), refresh: async () => { refreshed++; } }, {
    '../../lib/api': { ApiError, mobileApi: { sendChat: async payload => { posts.push(payload); return send(payload); } } },
    '../../lib/offlineQueue': {
      listPendingChats: async () => [...queue],
      enqueueChat: async (payload, uris, ownerId, mimeTypes) => { const item = { payload, imageUris: uris, imageMimeTypes: mimeTypes, ownerId, deliveryState: 'queued', createdAt: payload.occurred_at }; queue.push(item); enqueued.push(item); return item; },
      hydratePendingChat: async item => ({ ...item.payload, ...(item.imageUris?.length ? { images: item.imageUris.map((uri, i) => ({ image_base64: uri, mime_type: item.imageMimeTypes[i] })) } : {}) }),
      markPendingChat: async (key, state) => { const item = queue.find(item => item.payload.idempotency_key === key); if (item) item.deliveryState = state; },
      removePendingChat: async key => { const i = queue.findIndex(item => item.payload.idempotency_key === key); if (i >= 0) queue.splice(i, 1); },
    }, 'expo-crypto': { randomUUID: () => `key-${++serial}` },
    $appState: (_event, fn) => { foreground = fn; return { remove() {} }; },
    $globals: { Error, setTimeout: (fn, ms) => { const id = ++serial; timers.set(id, { fn, ms }); return id; }, clearTimeout: id => timers.delete(id) },
  });
  const retry = async () => { const [id, timer] = timers.entries().next().value; timers.delete(id); timer.fn(); await settled(); };
  return { view, queue, posts, errors, enqueued, timers, retry, data: () => data, refreshed: () => refreshed, foreground: () => foreground('active') };
}

test('unknown network state still attempts delivery; queue is persisted before the first POST', async () => {
  const state = delivery({ connected: null, send: async () => { assert.equal(state.queue.length, 1); return { status: 'succeeded', messages: [] }; } });
  state.view.render(); await settled(); assert.equal(await state.view.render().sendMessage('不太能吃辣'), 'sent');
  assert.equal(state.posts.length, 1); assert.equal(state.queue.length, 0); assert.equal(state.enqueued.length, 1);
  assert.equal(state.data().messages[0].pending, false); state.view.unmount();
});

test('definitely disconnected device queues without POST; recovery sends automatically without restart', async () => {
  const state = delivery({ connected: false }); state.view.render(); await settled();
  assert.equal(await state.view.render().sendMessage('晚睡习惯'), 'queued'); assert.equal(state.posts.length, 0);
  assert.equal(state.data().messages[0].delivery_state, 'queued'); assert.ok(state.timers.size);
  state.view.updateProps({ connected: true }); await settled(); assert.equal(state.posts.length, 1); assert.equal(state.queue.length, 0); state.view.unmount();
});

test('network failure retries the SAME key and timestamp without requiring a NetInfo change', async () => {
  let fail = true; const state = delivery({ send: async () => { if (fail) throw new ApiError(0, 'network_unavailable'); return { status: 'succeeded', messages: [] }; } });
  state.view.render(); await settled(); assert.equal(await state.view.render().sendMessage('习惯晚睡'), 'queued');
  fail = false; await state.retry(); assert.equal(state.posts.length, 2);
  assert.equal(state.posts[0].idempotency_key, state.posts[1].idempotency_key); assert.equal(state.posts[0].occurred_at, state.posts[1].occurred_at);
  assert.equal(state.queue.length, 0); assert.equal(state.enqueued.length, 1); state.view.unmount();
});

test('a processing response or timeout remains per-message processing, not a device disconnection', async () => {
  for (const send of [async () => ({ status: 'running' }), async () => { throw new ApiError(0, 'request_timeout'); }]) {
    const state = delivery({ send }); state.view.render(); await settled();
    assert.equal(await state.view.render().sendMessage('消息'), 'processing'); assert.equal(state.data().messages[0].delivery_state, 'processing');
    assert.equal(state.queue[0].deliveryState, 'processing'); assert.equal(state.errors.length, 0); assert.ok(state.timers.size); state.view.unmount();
  }
});

test('foreground retry and scheduled retry never issue overlapping POSTs for one key', async () => {
  const job = deferred(), state = delivery({ send: () => job.promise }); state.view.render(); await settled();
  const sending = state.view.render().sendMessage('消息'); await settled(); state.foreground(); await settled();
  assert.equal(state.posts.length, 1); job.resolve({ status: 'succeeded', messages: [] }); await sending;
  assert.equal(state.queue.length, 0); state.view.unmount();
});

test('a transient 503 keeps the outbox; a definite 401 error does not spin or become offline', async () => {
  let status = 503; const state = delivery({ send: async () => { throw new ApiError(status, 'request_failed'); } });
  state.view.render(); await settled(); assert.equal(await state.view.render().sendMessage('消息'), 'queued'); assert.equal(state.queue.length, 1);
  status = 401; await state.retry(); assert.equal(state.queue.length, 0); assert.equal(state.data().messages[0].failed, true);
  assert.ok(state.errors.length); state.view.unmount();
});

test('new image delivery and retries retain MIME types and do not copy photos repeatedly', async () => {
  let fail = true; const state = delivery({ send: async () => { if (fail) throw new ApiError(0, 'network_unavailable'); return { status: 'succeeded', messages: [] }; } });
  state.view.render(); await settled(); await state.view.render().sendMessage('图片', [{ uri: 'photo.png', mimeType: 'image/png' }]);
  fail = false; await state.retry(); assert.equal(state.posts[0].images[0].mime_type, 'image/png');
  assert.deepEqual(state.posts[0], state.posts[1]); assert.equal(state.enqueued.length, 1); state.view.unmount();
});

test('a new message cannot overtake an earlier in-flight message in the outbox', async () => {
  const job = deferred(); let count = 0;
  const state = delivery({ send: async () => ++count === 1 ? job.promise : { status: 'succeeded', messages: [] } });
  state.view.render(); await settled();
  const first = state.view.render().sendMessage('第一条'); await settled();
  assert.equal(await state.view.render().sendMessage('第二条'), 'queued');
  assert.equal(state.posts.length, 1);
  job.resolve({ status: 'succeeded', messages: [] }); await first;
  await state.retry();
  assert.deepEqual(state.posts.map(item => item.text), ['第一条', '第二条']);
  assert.equal(state.queue.length, 0); state.view.unmount();
});

test('server failures and throttling retain the original message for idempotent retry', async () => {
  for (const status of [500, 408, 429]) {
    const state = delivery({ send: async () => { throw new ApiError(status, 'temporary'); } });
    state.view.render(); await settled();
    assert.equal(await state.view.render().sendMessage('稍后重试'), 'queued');
    assert.equal(state.queue.length, 1); assert.equal(state.data().messages[0].failed, undefined);
    state.view.unmount();
  }
});

test('restored messages from another user are not posted or exposed in the current account', async () => {
  const state = delivery({ initialQueue: [{ ownerId: 'other', payload: { text: '别人的消息', idempotency_key: 'foreign' } }] });
  state.view.render(); await settled(); assert.equal(state.posts.length, 0); assert.equal(state.data().messages.length, 0); assert.equal(state.view.render().pending.length, 0); state.view.unmount();
});

test('late delivery results never modify a signed-out or different account UI', async () => {
  const job = deferred(), state = delivery({ send: () => job.promise }); state.view.render(); await settled();
  const sending = state.view.render().sendMessage('消息'); await settled(); state.view.updateProps({ userId: undefined });
  job.resolve({ status: 'succeeded', messages: [{ id: 'reply1', text: '旧账号的回复' }] }); await sending;
  assert.equal(state.data().messages.some(message => message.id === 'reply1'), false); assert.equal(state.refreshed(), 0); state.view.unmount();
});

test('durable queue serializes concurrent inserts and removals without dropping other messages', async () => {
  let storage = null; const view = fixture('lib/offlineQueue.ts', 'listPendingChats', {}, {
    '@react-native-async-storage/async-storage': { __esModule: true, default: { getItem: async () => storage, setItem: async (_key, value) => { await tick(); storage = value; } } },
    'expo-file-system': { Directory: class {}, Paths: { document: 'mock' }, File: class {} },
  });
  const queue = view.load(new URL('../src/lib/offlineQueue.ts', import.meta.url).pathname);
  await Promise.all(['a', 'b', 'c'].map(idempotency_key => queue.enqueueChat({ idempotency_key, text: idempotency_key }, [], 'u1')));
  assert.equal((await queue.listPendingChats()).length, 3);
  await Promise.all([queue.removePendingChat('a'), queue.markPendingChat('b', 'processing'), queue.enqueueChat({ idempotency_key: 'd' }, [], 'u1')]);
  const items = await queue.listPendingChats(); assert.deepEqual(Array.from(items, item => item.payload.idempotency_key).sort(), ['b', 'c', 'd']);
  assert.equal(items.find(item => item.payload.idempotency_key === 'b').deliveryState, 'processing');
});
