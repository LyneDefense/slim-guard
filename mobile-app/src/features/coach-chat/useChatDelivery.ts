import * as Crypto from 'expo-crypto';
import { useCallback, useEffect, useRef, useState } from 'react';
import { AppState } from 'react-native';
import { ApiError, mobileApi } from '../../lib/api';
import { enqueueChat, hydratePendingChat, listPendingChats, markPendingChat, removePendingChat } from '../../lib/offlineQueue';
import type { ChatMessage, ChatPayload, ChatResponse, DashboardData, PendingChat } from '../../types';
export type ImageInput = {
  uri: string;
  mimeType: 'image/jpeg' | 'image/png' | 'image/webp';
};
type Options = {
  userId?: string;
  connected: boolean | null;
  setData: (update: (data: DashboardData | null) => DashboardData | null) => void;
  onError: (message: string) => void;
  refresh: () => Promise<void>;
};
function optimistic(item: PendingChat): ChatMessage {
  return {
    id: `local-${item.payload.idempotency_key}`,
    turn_id: '',
    role: 'user',
    participant: 'user',
    kind: item.payload.text ? 'text' : 'image',
    text: item.payload.text || null,
    attachments: (item.imageUris || (item.imageUri ? [item.imageUri] : [])).map(uri => ({
      uri,
      mime_type: 'image/jpeg'
    })),
    created_at: item.payload.occurred_at || item.createdAt,
    pending: true,
    delivery_state: item.deliveryState || 'queued'
  };
}

// Owns durable delivery and retry, not authentication, business intent or network
// status. Every attempt retains the original key, timestamp and image files.
export function useChatDelivery(options: Options) {
  const latest = useRef(options);
  latest.current = options;
  const [pending, setPending] = useState<PendingChat[]>([]);
  const busy = useRef(new Set<string>()),
    flushing = useRef(false),
    alive = useRef(true);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null),
    delay = useRef(5_000);
  const drainRef = useRef<() => Promise<void>>(async () => {});
  const clearTimer = () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  };
  const schedule = useCallback(() => {
    if (!alive.current || !latest.current.userId || timer.current) return;
    timer.current = setTimeout(() => {
      timer.current = null;
      delay.current = Math.min(60_000, delay.current * 2);
      void drainRef.current();
    }, delay.current);
  }, []);
  const active = (owner: string) => alive.current && latest.current.userId === owner;
  function update(owner: string, action: (data: DashboardData) => DashboardData) {
    if (active(owner)) latest.current.setData(data => data?.user.id === owner ? action(data) : data);
  }
  async function reload(owner: string) {
    const queue = (await listPendingChats()).filter(item => !item.ownerId || item.ownerId === owner);
    if (active(owner)) setPending(queue);
    return queue;
  }
  async function syncPending(owner: string) {
    try {
      return await reload(owner);
    } catch (caught) {
      if (active(owner)) latest.current.onError(caught instanceof Error ? caught.message : '读取待发送消息失败');
      schedule();
      return [];
    }
  }
  async function phase(owner: string, key: string, state: 'sending' | 'queued' | 'processing') {
    if (state !== 'sending') await markPendingChat(key, state);
    update(owner, data => ({
      ...data,
      messages: data.messages.map(message => message.id === `local-${key}` ? {
        ...message,
        pending: true,
        delivery_state: state
      } : message)
    }));
  }
  function complete(owner: string, key: string, response: ChatResponse) {
    update(owner, data => {
      const existing = data.messages.map(message => message.id === `local-${key}` ? {
        ...message,
        pending: false,
        delivery_state: undefined
      } : message);
      const replies: ChatMessage[] = response.messages?.length ? response.messages : response.text ? [{
        id: `reply-${response.request_id}`,
        turn_id: response.turn_id || '',
        participant: 'system_assistant',
        role: 'assistant',
        kind: 'text',
        text: response.text,
        created_at: new Date().toISOString()
      }] : [];
      const ids = new Set(existing.map(message => message.id));
      return {
        ...data,
        messages: [...existing, ...replies.filter(message => !ids.has(message.id))]
      };
    });
  }
  function fail(owner: string, key: string, message: string) {
    update(owner, data => ({
      ...data,
      messages: data.messages.map(item => item.id === `local-${key}` ? {
        ...item,
        pending: false,
        failed: true,
        delivery_state: undefined
      } : item)
    }));
    if (active(owner)) latest.current.onError(message);
  }
  async function dispatch(owner: string, item: PendingChat): Promise<'sent' | 'queued' | 'processing'> {
    const key = item.payload.idempotency_key;
    try {
      await phase(owner, key, item.deliveryState === 'processing' ? 'processing' : 'sending');
      const payload = await hydratePendingChat(item);
      if (!active(owner) || latest.current.connected === false) {
        schedule();
        return 'queued';
      }
      const response = await mobileApi.sendChat(payload);
      if (response.status === 'running') {
        await phase(owner, key, 'processing');
        schedule();
        return 'processing';
      }
      await removePendingChat(key);
      delay.current = 5_000;
      if (response.status === 'failed') {
        fail(owner, key, `教练这次没有处理成功（${response.failure_code || '未知原因'}），请重试`);
        return 'sent';
      }
      complete(owner, key, response);
      return 'sent';
    } catch (caught) {
      if (caught instanceof ApiError && (caught.status === 0 || caught.status >= 500 || [408, 429].includes(caught.status))) {
        const state = caught.code === 'request_timeout' || item.deliveryState === 'processing' ? 'processing' : 'queued';
        await phase(owner, key, state);
        schedule();
        return state;
      }
      await removePendingChat(key);
      fail(owner, key, caught instanceof Error ? caught.message : '发送失败，请重试');
      throw caught;
    } finally {
      await syncPending(owner);
    }
  }
  const drain = useCallback(async () => {
    const {
      userId: owner,
      connected
    } = latest.current;
    if (!owner || !alive.current || flushing.current) return;
    clearTimer();
    if (connected === false) {
      if ((await syncPending(owner)).length) schedule();
      return;
    }
    flushing.current = true;
    let attempted = false;
    try {
      for (const item of await reload(owner)) {
        if (!active(owner) || latest.current.connected === false) break;
        const key = item.payload.idempotency_key;
        if (busy.current.has(key)) {
          schedule();
          break;
        }
        busy.current.add(key);
        attempted = true;
        try {
          if ((await dispatch(owner, item)) !== 'sent') break;
        } catch {/* Definite failure was surfaced; continue with other queued messages. */} finally {
          busy.current.delete(key);
        }
      }
    } catch (caught) {
      if (active(owner)) latest.current.onError(caught instanceof Error ? caught.message : '读取待发送消息失败');
      schedule();
    } finally {
      flushing.current = false;
      if (attempted && active(owner)) void latest.current.refresh();
      if (active(owner) && (await syncPending(owner)).length) schedule();
    }
  }, [schedule]);
  drainRef.current = drain;
  useEffect(() => {
    alive.current = true;
    const foreground = AppState.addEventListener('change', state => {
      if (state === 'active') void drainRef.current();
    });
    return () => {
      alive.current = false;
      clearTimer();
      foreground.remove();
    };
  }, []);
  useEffect(() => {
    clearTimer();
    delay.current = 5_000;
    if (!options.userId) {
      setPending([]);
      return;
    }
    const owner = options.userId;
    void reload(owner).then(queue => {
      update(owner, data => {
        const ids = new Set(data.messages.map(message => message.id));
        return {
          ...data,
          messages: [...data.messages, ...queue.map(optimistic).filter(message => !ids.has(message.id))]
        };
      });
      if (queue.length && active(owner)) void drainRef.current();
    }).catch(() => {
      if (active(owner)) latest.current.onError('读取待发送消息失败');
    });
    return clearTimer;
  }, [options.userId]);
  useEffect(() => {
    if (options.connected !== false) void drainRef.current();
  }, [options.connected]);
  const sendMessage = useCallback(async (text: string, images: ImageInput[] = []): Promise<'sent' | 'queued' | 'processing'> => {
    const owner = latest.current.userId;
    if (!owner) throw new Error('请先登录');
    const key = Crypto.randomUUID();
    busy.current.add(key);
    try {
      const payload: ChatPayload = {
        ...(text.trim() ? {
          text: text.trim()
        } : {}),
        idempotency_key: key,
        occurred_at: new Date().toISOString()
      };
      const item = await enqueueChat(payload, images.map(image => image.uri), owner, images.map(image => image.mimeType));
      update(owner, data => ({
        ...data,
        messages: [...data.messages, {
          ...optimistic(item),
          attachments: images.map(image => ({
            uri: image.uri,
            mime_type: image.mimeType
          }))
        }]
      }));
      const queue = await reload(owner);
      if (!active(owner) || latest.current.connected === false || flushing.current || queue[0]?.payload.idempotency_key !== key) {
        schedule();
        return 'queued';
      }
      const result = await dispatch(owner, item);
      if (result === 'sent' && active(owner)) void latest.current.refresh();
      return result;
    } finally {
      busy.current.delete(key);
    }
  }, [schedule]);
  return {
    pending,
    sendMessage
  };
}
