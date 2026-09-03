import NetInfo from '@react-native-community/netinfo';
import * as Crypto from 'expo-crypto';
import { File } from 'expo-file-system';
import { Platform } from 'react-native';
import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';

import { ApiError, mobileApi } from '../lib/api';
import { enqueueChat, hydratePendingChat, listPendingChats, removePendingChat } from '../lib/offlineQueue';
import { syncRoutineNotifications } from '../lib/notifications';
import type {
  AuthTokens,
  ChatMessage,
  ChatPayload,
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
  data: DashboardData | null;
  pending: PendingChat[];
  online: boolean;
  loading: boolean;
  error: string | null;
  requestOtp: (phone: string) => Promise<OtpChallenge>;
  verifyOtp: (challengeId: string, code: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
  sendMessage: (text: string, image?: ImageInput) => Promise<'sent' | 'queued'>;
  updateProfile: (nickname: string) => Promise<void>;
  updateRoutine: (routine: Routine) => Promise<boolean>;
  createWeComBinding: () => Promise<WeComBinding>;
  getWeComBinding: () => Promise<WeComBinding | null>;
  deleteAccount: () => Promise<void>;
  clearError: () => void;
};

const AppContext = createContext<AppContextValue | null>(null);

function errorMessage(error: unknown): string {
  if (error instanceof ApiError || error instanceof Error) return error.message;
  return '刚才没有成功，请稍后再试';
}

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [booting, setBooting] = useState(true);
  const [tokens, setTokens] = useState<AuthTokens | null>(null);
  const [data, setData] = useState<DashboardData | null>(null);
  const [pending, setPending] = useState<PendingChat[]>([]);
  const [online, setOnline] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const flushing = useRef(false);

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
      const [restored, queued] = await Promise.all([mobileApi.restore(), listPendingChats()]);
      if (!active) return;
      setTokens(restored);
      setPending(queued);
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
    flushing.current = true;
    try {
      const queue = await listPendingChats();
      for (const item of queue) {
        try {
          await mobileApi.sendChat(await hydratePendingChat(item));
          await removePendingChat(item.payload.idempotency_key);
        } catch (caught) {
          if (caught instanceof ApiError && caught.status === 0) break;
          await removePendingChat(item.payload.idempotency_key);
          setError(`一条离线消息发送失败：${errorMessage(caught)}`);
        }
      }
      setPending(await listPendingChats());
      setData(await mobileApi.dashboard());
    } finally {
      flushing.current = false;
    }
  }, [tokens]);

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

  const sendMessage = useCallback(async (text: string, image?: ImageInput): Promise<'sent' | 'queued'> => {
    const idempotencyKey = Crypto.randomUUID();
    const payload: ChatPayload = {
      ...(text.trim() ? { text: text.trim() } : {}),
      ...(image ? { image_mime_type: image.mimeType } : {}),
      idempotency_key: idempotencyKey,
      occurred_at: new Date().toISOString(),
    };
    const optimistic: ChatMessage = {
      id: `local-${idempotencyKey}`,
      turn_id: '',
      role: 'user',
      kind: image ? 'image' : 'text',
      text: text.trim() || (image ? '📷 饮食照片' : null),
      created_at: payload.occurred_at,
      pending: true,
    };
    setData((current) => current ? { ...current, messages: [...current.messages, optimistic] } : current);

    if (!online) {
      await enqueueChat(payload, image?.uri);
      setPending(await listPendingChats());
      return 'queued';
    }
    try {
      if (image) payload.image_base64 = await new File(image.uri).base64();
      const response = await mobileApi.sendChat(payload);
      if (response.status === 'failed') throw new Error('教练这次没有处理成功，请重试');
      setData((current) => current ? {
        ...current,
        messages: [
          ...current.messages.map((message) => message.id === optimistic.id ? { ...message, pending: false } : message),
          ...(response.text ? [{
            id: `reply-${response.request_id}`,
            turn_id: response.turn_id || '',
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
        await enqueueChat(payload, image?.uri);
        setPending(await listPendingChats());
        setOnline(false);
        return 'queued';
      }
      setData((current) => current ? {
        ...current,
        messages: current.messages.map((message) => message.id === optimistic.id ? { ...message, pending: false, failed: true } : message),
      } : current);
      setError(errorMessage(caught));
      throw caught;
    }
  }, [online, refresh]);

  const updateProfile = useCallback(async (nickname: string) => {
    const user = await mobileApi.updateProfile(nickname.trim() || null);
    setData((current) => current ? { ...current, user } : current);
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
    data,
    pending,
    online,
    loading,
    error,
    requestOtp,
    verifyOtp,
    logout,
    refresh,
    sendMessage,
    updateProfile,
    updateRoutine,
    createWeComBinding,
    getWeComBinding,
    deleteAccount,
    clearError: () => setError(null),
  }), [booting, tokens, data, pending, online, loading, error, requestOtp, verifyOtp, logout, refresh, sendMessage, updateProfile, updateRoutine, createWeComBinding, getWeComBinding, deleteAccount]);

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp(): AppContextValue {
  const value = useContext(AppContext);
  if (!value) throw new Error('useApp must be used inside AppProvider');
  return value;
}
