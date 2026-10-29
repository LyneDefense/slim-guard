import { Platform } from 'react-native';
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';

import { ApiError, mobileApi } from '../lib/api';
import { syncRoutineNotifications } from '../lib/notifications';
import { useChatDelivery } from '../features/coach-chat/useChatDelivery';
import type { ImageInput } from '../features/coach-chat/useChatDelivery';
import { useConnection } from '../shared/network/useConnection';
import type { ConnectionState } from '../shared/network/connection';
import { brand } from '../shared/brand/identity';
import { useSystemNotices } from '../features/system-notices/useSystemNotices';
import type { SystemNotice } from '../features/system-notices/noticeFeed';
import type {
  AuthOptions,
  AuthTokens,
  CoachProfileInput,
  CoachProfileStatus,
  DashboardData,
  OtpChallenge,
  PendingChat,
  Routine,
  WeComBinding,
} from '../types';

type AppContextValue = {
  booting: boolean;
  authenticated: boolean;
  authOptions: AuthOptions;
  data: DashboardData | null;
  pending: PendingChat[];
  online: boolean;
  connection: ConnectionState;
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
  const connection = useConnection();
  const online = connection.device !== 'disconnected' && connection.backend !== 'unavailable';
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { notices, dismissNotice } = useSystemNotices(data?.messages, data?.user.id);
  const refresh = useCallback(async () => {
    const owner = data?.user.id;
    if (!owner) return;
    setLoading(true);
    try {
      const incoming = await mobileApi.dashboard();
      setData(current => {
        if (!current || current.user.id !== owner || incoming.user.id !== owner) return current;
        const ids = new Set(incoming.messages.map(message => message.id));
        return { ...incoming, messages: [...incoming.messages, ...current.messages.filter(message => message.pending && !ids.has(message.id))] };
      });
      setError(null);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setLoading(false);
    }
  }, [data?.user.id]);

  const { pending, sendMessage } = useChatDelivery({
    userId: tokens ? data?.user.id : undefined,
    connected: connection.device === 'unknown' ? null : connection.device === 'connected',
    setData, onError: setError, refresh,
  });

  useEffect(() => mobileApi.onAuthChanged((next) => {
    setTokens(next);
    if (!next) setData(null);
  }), []);

  useEffect(() => {
    let active = true;
    void (async () => {
      const [restored, options] = await Promise.all([
        mobileApi.restore(),
        mobileApi.authOptions().catch(() => PHONE_ONLY_AUTH),
      ]);
      if (!active) return;
      setTokens(restored);
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

  const requestOtp = useCallback(async (phone: string) => {
    setError(null);
    return mobileApi.requestOtp(phone);
  }, []);

  const verifyOtp = useCallback(async (challengeId: string, code: string) => {
    setLoading(true);
    try {
      const issued = await mobileApi.verifyOtp(challengeId, code, `${Platform.OS} · ${brand.name}`);
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
        `${Platform.OS} · ${brand.name}`,
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

  const updateProfile = useCallback(async (nickname: string) => {
    const user = await mobileApi.updateProfile(nickname.trim() || null);
    setData((current) => current && current.user.id === user.id ? { ...current, user } : current);
  }, []);

  const saveCoachProfile = useCallback(async (profile: CoachProfileInput) => {
    const userId = data?.user.id;
    const saved = await mobileApi.saveCoachProfile(profile);
    // PUT already returns the committed profile. A subsequent dashboard read
    // must not turn an accepted write into a false save failure.
    setData((current) => current && current.user.id === userId ? { ...current, coach_profile: saved } : current);
    return saved;
  }, [data?.user.id]);

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
    connection,
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
  }), [booting, tokens, authOptions, data, pending, online, connection, loading, error, notices, dismissNotice, requestOtp, verifyOtp, loginWithPassword, logout, refresh, sendMessage, updateProfile, saveCoachProfile, updateRoutine, createWeComBinding, getWeComBinding, deleteAccount]);

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp(): AppContextValue {
  const value = useContext(AppContext);
  if (!value) throw new Error('useApp must be used inside AppProvider');
  return value;
}
