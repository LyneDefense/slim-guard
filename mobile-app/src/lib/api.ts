import type {
  AuthTokens,
  ChatMessage,
  ChatPayload,
  ChatResponse,
  DashboardData,
  MemoryItem,
  MobileUser,
  OtpChallenge,
  Progress,
  Routine,
  Today,
} from '../types';
import { clearRefreshToken, readRefreshToken, saveRefreshToken } from './session';

const configuredBaseUrl = process.env.EXPO_PUBLIC_API_BASE_URL?.trim();
export const API_BASE_URL = (configuredBaseUrl || 'http://127.0.0.1:8000').replace(/\/$/, '');

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

type ApiErrorBody = { detail?: string | { code?: string; message?: string } };

class MobileApi {
  private accessToken: string | null = null;
  private refreshInFlight: Promise<AuthTokens> | null = null;
  private authChanged: ((tokens: AuthTokens | null) => void) | null = null;

  onAuthChanged(listener: (tokens: AuthTokens | null) => void): () => void {
    this.authChanged = listener;
    return () => {
      if (this.authChanged === listener) this.authChanged = null;
    };
  }

  requestOtp(phone: string): Promise<OtpChallenge> {
    return this.raw('/api/mobile/v1/auth/otp/request', {
      method: 'POST',
      body: JSON.stringify({ phone }),
    });
  }

  async verifyOtp(challengeId: string, code: string, deviceLabel: string): Promise<AuthTokens> {
    const tokens = await this.raw<AuthTokens>('/api/mobile/v1/auth/otp/verify', {
      method: 'POST',
      body: JSON.stringify({ challenge_id: challengeId, code, device_label: deviceLabel }),
    });
    await this.acceptTokens(tokens);
    return tokens;
  }

  async restore(): Promise<AuthTokens | null> {
    const token = await readRefreshToken();
    if (!token) return null;
    try {
      return await this.refresh(token);
    } catch {
      await this.clearSession();
      return null;
    }
  }

  async logout(): Promise<void> {
    try {
      if (this.accessToken) {
        await this.authorized<void>('/api/mobile/v1/auth/logout', { method: 'POST' }, false);
      }
    } finally {
      await this.clearSession();
    }
  }

  async dashboard(): Promise<DashboardData> {
    const [user, today, progress, memories, routine, history] = await Promise.all([
      this.authorized<MobileUser>('/api/mobile/v1/me'),
      this.authorized<Today>('/api/mobile/v1/today'),
      this.authorized<Progress>('/api/mobile/v1/progress?limit=60'),
      this.authorized<MemoryItem[]>('/api/mobile/v1/memories'),
      this.authorized<Routine>('/api/mobile/v1/routine'),
      this.authorized<{ items: ChatMessage[] }>('/api/mobile/v1/chat/messages?limit=100'),
    ]);
    return { user, today, progress, memories, routine, messages: history.items };
  }

  sendChat(payload: ChatPayload): Promise<ChatResponse> {
    return this.authorized('/api/mobile/v1/chat/messages', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  updateProfile(nickname: string | null): Promise<MobileUser> {
    return this.authorized('/api/mobile/v1/me', {
      method: 'PATCH',
      body: JSON.stringify({ nickname }),
    });
  }

  updateRoutine(routine: Routine): Promise<Routine> {
    const setting = (value: string | null) => ({ enabled: value !== null, local_time: value });
    return this.authorized('/api/mobile/v1/routine', {
      method: 'PUT',
      body: JSON.stringify({
        timezone: routine.timezone,
        weight: setting(routine.weight_reminder_time),
        meal: setting(routine.meal_reminder_time),
        daily_review: setting(routine.daily_review_time),
      }),
    });
  }

  private async authorized<T>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
    if (!this.accessToken) throw new ApiError(401, 'authentication_required', '请先登录');
    try {
      return await this.raw<T>(path, init, this.accessToken);
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 401 || !retry) throw error;
      const refreshToken = await readRefreshToken();
      if (!refreshToken) {
        await this.clearSession();
        throw error;
      }
      await this.refresh(refreshToken);
      return this.authorized<T>(path, init, false);
    }
  }

  private async refresh(token: string): Promise<AuthTokens> {
    if (!this.refreshInFlight) {
      this.refreshInFlight = this.raw<AuthTokens>('/api/mobile/v1/auth/refresh', {
        method: 'POST',
        body: JSON.stringify({ refresh_token: token }),
      })
        .then(async (tokens) => {
          await this.acceptTokens(tokens);
          return tokens;
        })
        .finally(() => {
          this.refreshInFlight = null;
        });
    }
    return this.refreshInFlight;
  }

  private async acceptTokens(tokens: AuthTokens): Promise<void> {
    this.accessToken = tokens.access_token;
    await saveRefreshToken(tokens.refresh_token);
    this.authChanged?.(tokens);
  }

  private async clearSession(): Promise<void> {
    this.accessToken = null;
    await clearRefreshToken();
    this.authChanged?.(null);
  }

  private async raw<T>(path: string, init: RequestInit, bearer?: string): Promise<T> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 30_000);
    try {
      const response = await fetch(`${API_BASE_URL}${path}`, {
        ...init,
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
          ...(bearer ? { Authorization: `Bearer ${bearer}` } : {}),
          ...(init.headers || {}),
        },
        signal: controller.signal,
      });
      if (!response.ok) {
        let body: ApiErrorBody = {};
        try {
          body = (await response.json()) as ApiErrorBody;
        } catch {
          // Reverse proxies can return HTML; the HTTP status remains useful.
        }
        const detail = body.detail;
        const code = typeof detail === 'object' ? detail.code : undefined;
        const message = typeof detail === 'object' ? detail.message : detail;
        throw new ApiError(response.status, code || 'request_failed', message || '请求没有成功');
      }
      if (response.status === 204) return undefined as T;
      return (await response.json()) as T;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (error instanceof Error && error.name === 'AbortError') {
        throw new ApiError(0, 'request_timeout', '网络有点慢，请稍后再试');
      }
      throw new ApiError(0, 'network_unavailable', '暂时连不上服务器');
    } finally {
      clearTimeout(timeout);
    }
  }
}

export const mobileApi = new MobileApi();
