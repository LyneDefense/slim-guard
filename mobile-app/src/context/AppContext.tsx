import NetInfo from '@react-native-community/netinfo';
import * as Crypto from 'expo-crypto';
import { File } from 'expo-file-system';
import { Platform } from 'react-native';
import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';

import { ApiError, mobileApi } from '../lib/api';
import { enqueueChat, hydratePendingChat, listPendingChats, removePendingChat } from '../lib/offlineQueue';
import { syncRoutineNotifications } from '../lib/notifications';
import { useSystemNotices } from '../features/system-notices/useSystemNotices';
import type { SystemNotice } from '../features/system-notices/noticeFeed';
import type {
  AuthOptions,
  AuthTokens,
  ChatMessage,
  ChatPayload,
  CoachProfileInput,
  CoachProfileStatus,
  DashboardData,
  OtpChallenge,
  PendingChat,
  Routine,
  WeComBinding,
} from '../types';

type ImageInput = { uri: string; mimeType: 'image/jpeg' | 'image/png' | 'image/webp' };

type AppContextValue = {
  booting: boolean;
  authenticated: boolean;
  authOptions: AuthOptions;
  data: DashboardData | null;
  pending: PendingChat[];
  online: boolean;
  loading: boolean;
  error: string | null;
  notices: SystemNotice[];
  dismissNotice: (id: string) => void;
  requestOtp: (phone: string) => Promise<OtpChallenge>;
  verifyOtp: (challengeId: string, code: string) => Promise<void>;
  loginWithPassword: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
  sendMessage: (text: string, images?: ImageInput[]) => Promise<'sent' | 'queued' | 'processing'>;
  updateProfile: (nickname: string) => Promise<void>;
  saveCoachProfile: (profile: CoachProfileInput) => Promise<CoachProfileStatus>;
  updateRoutine: (routine: Routine) => Promise<boolean>;
  createWeComBinding: () => Promise<WeComBinding>;
  getWeComBinding: () => Promise<WeComBinding | null>;
  deleteAccount: () => Promise<void>;
  clearError: () => void;
};

const AppContext = createContext<AppContextValue | null>(null);

const PHONE_ONLY_AUTH: AuthOptions = {
  phone_login_enabled: true,
  test_account_login_enabled: false,
  test_accounts: [],
};

function errorMessage(error: unknown): string {
  if (error instanceof ApiError || error instanceof Error) return error.message;
  return '刚才没有成功，请稍后再试';
}

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [booting, setBooting] = useState(true);
  const [tokens, setTokens] = useState<AuthTokens | null>(null);
  const [authOptions, setAuthOptions] = useState<AuthOptions>(PHONE_ONLY_AUTH);
  const [data, setData] = useState<DashboardData | null>(null);
  const [pending, setPending] = useState<PendingChat[]>([]);
  const [online, setOnline] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { notices, dismissNotice } = useSystemNotices(data?.messages, data?.user.id);
  const flushing = useRef(false);
  const queueRetryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const queueRetryDelay = useRef(5_000);
  const flushQueueRef = useRef<(() => Promise<void>) | null>(null);

  const scheduleQueueRetry = useCallback(() => {
    if (!tokens || !online || queueRetryTimer.current) return;
    const delayMs = queueRetryDelay.current;
    queueRetryTimer.current = setTimeout(() => {
      queueRetryTimer.current = null;
      queueRetryDelay.current = Math.min(60_000, queueRetryDelay.current * 2);
      void flushQueueRef.current?.();
    }, delayMs);
  }, [online, tokens]);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setData(await mobileApi.dashboard());
      setError(null);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => mobileApi.onAuthChanged((next) => {
    setTokens(next);
    if (!next) setData(null);
  }), []);

  useEffect(() => {
    let active = true;
    void (async () => {
      const [restored, queued, options] = await Promise.all([
        mobileApi.restore(),
        listPendingChats(),
        mobileApi.authOptions().catch(() => PHONE_ONLY_AUTH),
      ]);
      if (!active) return;
      setTokens(restored);
      setPending(queued);
      setAuthOptions(options);
      if (restored) {
        try {
          setData(await mobileApi.dashboard());
        } catch (caught) {
          setError(errorMessage(caught));
        }
      }
      setBooting(false);
    })();
    return () => { active = false; };
  }, []);

  const flushQueue = useCallback(async () => {
    if (flushing.current || !tokens) return;
    if (queueRetryTimer.current) {
      clearTimeout(queueRetryTimer.current);
      queueRetryTimer.current = null;
    }
    flushing.current = true;
    try {
      const queue = await listPendingChats();
      for (const item of queue) {
        try {
          const response = await mobileApi.sendChat(await hydratePendingChat(item));
          if (response.status === 'running') {
            scheduleQueueRetry();
            break;
          }
          await removePendingChat(item.payload.idempotency_key);
          queueRetryDelay.current = 5_000;
          if (response.status === 'failed') {
            const localId = `local-${item.payload.idempotency_key}`;
            setData((current) => current ? {
              ...current,
              messages: current.messages.map((message) => message.id === localId
                ? { ...message, pending: false, failed: true }
                : message),
            } : current);
            setError(`一条待发送消息处理失败（${response.failure_code || '未知原因'}），请检查后重试`);
          }
        } catch (caught) {
          if (caught instanceof ApiError && caught.status === 0) {
            scheduleQueueRetry();
            break;
          }
          await removePendingChat(item.payload.idempotency_key);
          const localId = `local-${item.payload.idempotency_key}`;
          setData((current) => current ? {
            ...current,
            messages: current.messages.map((message) => message.id === localId
              ? { ...message, pending: false, failed: true }
              : message),
          } : current);
          setError(`一条离线消息发送失败：${errorMessage(caught)}`);
        }
      }
      setPending(await listPendingChats());
      try {
        setData(await mobileApi.dashboard());
      } catch (caught) {
        setError(errorMessage(caught));
      }
    } finally {
      flushing.current = false;
    }
  }, [scheduleQueueRetry, tokens]);

  flushQueueRef.current = flushQueue;

  useEffect(() => () => {
    if (queueRetryTimer.current) clearTimeout(queueRetryTimer.current);
  }, []);

  useEffect(() => NetInfo.addEventListener((state) => {
    const connected = state.isConnected === true && state.isInternetReachable !== false;
    setOnline(connected);
    if (connected) void flushQueue();
  }), [flushQueue]);

  const requestOtp = useCallback(async (phone: string) => {
    setError(null);
    return mobileApi.requestOtp(phone);
  }, []);

  const verifyOtp = useCallback(async (challengeId: string, code: string) => {
    setLoading(true);
    try {
      const issued = await mobileApi.verifyOtp(challengeId, code, `${Platform.OS} · SlimGuard`);
      setTokens(issued);
      setData(await mobileApi.dashboard());
      setError(null);
    } catch (caught) {
      setError(errorMessage(caught));
      throw caught;
    } finally {
      setLoading(false);
    }
  }, []);

  const loginWithPassword = useCallback(async (username: string, password: string) => {
    setLoading(true);
    try {
      const issued = await mobileApi.loginWithPassword(
        username,
        password,
        `${Platform.OS} · SlimGuard`,
      );
      setTokens(issued);
      setData(await mobileApi.dashboard());
      setError(null);
    } catch (caught) {
      setError(errorMessage(caught));
      throw caught;
    } finally {
      setLoading(false);
    }
  }, []);

  const logout = useCallback(async () => {
    setLoading(true);
    try {
      await mobileApi.logout();
      setTokens(null);
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const sendMessage = useCallback(async (text: string, images: ImageInput[] = []): Promise<'sent' | 'queued' | 'processing'> => {
    const idempotencyKey = Crypto.randomUUID();
    const payload: ChatPayload = {
      ...(text.trim() ? { text: text.trim() } : {}),
      idempotency_key: idempotencyKey,
      occurred_at: new Date().toISOString(),
    };
    const optimistic: ChatMessage = {
      id: `local-${idempotencyKey}`,
      turn_id: '',
      participant: 'user',
      role: 'user',
      kind: text.trim() ? 'text' : 'image',
      text: text.trim() || null,
      attachments: images.map((image) => ({ uri: image.uri, mime_type: image.mimeType })),
      created_at: payload.occurred_at,
      pending: true,
    };
    setData((current) => current ? { ...current, messages: [...current.messages, optimistic] } : current);

    if (!online) {
      await enqueueChat(payload, images.map((image) => image.uri));
      setPending(await listPendingChats());
      return 'queued';
    }
    try {
      if (images.length) {
        payload.images = await Promise.all(images.map(async (image) => ({
          image_base64: await new File(image.uri).base64(),
          mime_type: image.mimeType,
        })));
      }
      const response = await mobileApi.sendChat(payload);
      if (response.status === 'failed') throw new Error('教练这次没有处理成功，请重试');
      if (response.status === 'running') {
        await enqueueChat(payload, images.map((image) => image.uri));
        setPending(await listPendingChats());
        scheduleQueueRetry();
        return 'processing';
      }
      setData((current) => current ? {
        ...current,
        messages: [
          ...current.messages.map((message) => message.id === optimistic.id ? { ...message, pending: false } : message),
          ...(response.messages?.length ? response.messages : response.text ? [{
            id: `reply-${response.request_id}`,
            turn_id: response.turn_id || '',
            participant: 'system_assistant' as const,
            role: 'assistant' as const,
            kind: 'text' as const,
            text: response.text,
            created_at: new Date().toISOString(),
          }] : []),
        ],
      } : current);
      void refresh();
      return 'sent';
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 0) {
        await enqueueChat(payload, images.map((image) => image.uri));
        setPending(await listPendingChats());
        scheduleQueueRetry();
        if (caught.code === 'request_timeout') {
          return 'processing';
        }
        return 'queued';
      }
      setData((current) => current ? {
        ...current,
        messages: current.messages.map((message) => message.id === optimistic.id ? { ...message, pending: false, failed: true } : message),
      } : current);
      setError(errorMessage(caught));
      throw caught;
    }
  }, [online, refresh, scheduleQueueRetry]);

  const updateProfile = useCallback(async (nickname: string) => {
    const user = await mobileApi.updateProfile(nickname.trim() || null);
    setData((current) => current ? { ...current, user } : current);
  }, []);

  const saveCoachProfile = useCallback(async (profile: CoachProfileInput) => {
    const saved = await mobileApi.saveCoachProfile(profile);
    setData(await mobileApi.dashboard());
    return saved;
  }, []);

  const updateRoutine = useCallback(async (routine: Routine) => {
    const saved = await mobileApi.updateRoutine(routine);
    setData((current) => current ? { ...current, routine: saved, today: { ...current.today, routine: saved } } : current);
    return syncRoutineNotifications(saved);
  }, []);

  const createWeComBinding = useCallback(() => mobileApi.createWeComBinding(), []);
  const getWeComBinding = useCallback(() => mobileApi.getWeComBinding(), []);
  const deleteAccount = useCallback(async () => {
    setLoading(true);
    try {
      await mobileApi.deleteAccount();
      setTokens(null);
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const value = useMemo<AppContextValue>(() => ({
    booting,
    authenticated: tokens !== null,
    authOptions,
    data,
    pending,
    online,
    loading,
    error,
    notices,
    dismissNotice,
    requestOtp,
    verifyOtp,
    loginWithPassword,
    logout,
    refresh,
    sendMessage,
    updateProfile,
    saveCoachProfile,
    updateRoutine,
    createWeComBinding,
    getWeComBinding,
    deleteAccount,
    clearError: () => setError(null),
  }), [booting, tokens, authOptions, data, pending, online, loading, error, notices, dismissNotice, requestOtp, verifyOtp, loginWithPassword, logout, refresh, sendMessage, updateProfile, saveCoachProfile, updateRoutine, createWeComBinding, getWeComBinding, deleteAccount]);

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp(): AppContextValue {
  const value = useContext(AppContext);
  if (!value) throw new Error('useApp must be used inside AppProvider');
  return value;
}
