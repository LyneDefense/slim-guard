import type {
  AuthOptions,
  AuthTokens,
  ChatAttachment,
  ChatMessage,
  ChatPayload,
  ChatResponse,
  CoachProfileInput,
  CoachProfileStatus,
  DashboardData,
  MemoryItem,
  MobileUser,
  OtpChallenge,
  Routine,
  Today,
  DeviceRegistration,
  WeComBinding,
} from '../types';
import { clearRefreshToken, readRefreshToken, saveRefreshToken } from './session';
import { connectionMonitor } from '../shared/network/connection';

const configuredBaseUrl = process.env.EXPO_PUBLIC_API_BASE_URL?.trim();
export const API_BASE_URL = (configuredBaseUrl || 'http://127.0.0.1:8000').replace(/\/$/, '');
const DEFAULT_REQUEST_TIMEOUT_MS = 30_000;
// Leave headroom under the proxy's 90s read timeout; uncertain outcomes are reconciled by key.
const CHAT_REQUEST_TIMEOUT_MS = 75_000;
const CHAT_STATUS_POLL_TIMEOUT_MS = 10_000;
const CHAT_STATUS_POLL_WINDOW_MS = 60_000;

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
  request<T>(path: string, init: RequestInit = {}): Promise<T> {
    return this.authorized<T>(path, init);
  }
  private accessToken: string | null = null;
  private refreshInFlight: Promise<AuthTokens> | null = null;
  private authChanged: ((tokens: AuthTokens | null) => void) | null = null;

  imageSource(attachment: ChatAttachment): { uri: string; headers?: Record<string, string> } | null {
    if (attachment.uri) return { uri: attachment.uri };
    const path = attachment.url || (attachment.asset_id ? `/api/mobile/v1/chat/assets/${encodeURIComponent(attachment.asset_id)}` : null);
    if (!path) return null;
    return {
      uri: `${API_BASE_URL}${path}`,
      ...(this.accessToken ? { headers: { Authorization: `Bearer ${this.accessToken}` } } : {}),
    };
  }

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

  authOptions(): Promise<AuthOptions> {
    return this.raw('/api/mobile/v1/auth/options', { method: 'GET' });
  }

  async loginWithPassword(
    username: string,
    password: string,
    deviceLabel: string,
  ): Promise<AuthTokens> {
    const tokens = await this.raw<AuthTokens>('/api/mobile/v1/auth/password/login', {
      method: 'POST',
      body: JSON.stringify({ username, password, device_label: deviceLabel }),
    });
    await this.acceptTokens(tokens);
    return tokens;
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
    const [user, coachProfile, today, memories, routine, history] = await Promise.all([
      this.authorized<MobileUser>('/api/mobile/v1/me'),
      this.authorized<CoachProfileStatus>('/api/mobile/v1/coach-profile'),
      this.authorized<Today>('/api/mobile/v1/today'),
      this.authorized<MemoryItem[]>('/api/mobile/v1/memories'),
      this.authorized<Routine>('/api/mobile/v1/routine'),
      this.authorized<{ items: ChatMessage[] }>('/api/mobile/v1/chat/messages?limit=100'),
    ]);
    return { user, coach_profile: coachProfile, today, memories, routine, messages: history.items };
  }

  async sendChat(payload: ChatPayload): Promise<ChatResponse> {
    let response: ChatResponse;
    try {
      response = await this.postChat(payload);
    } catch (error) {
      if (!this.isUncertainChatOutcome(error)) throw error;
      try {
        response = await this.getChatRequest(payload.idempotency_key);
      } catch (statusError) {
        if (!(statusError instanceof ApiError) || statusError.status !== 404) throw error;
        // The first POST may have failed before the server claimed the key.
        // Replaying the identical payload with the same key is safe and closes that race.
        response = await this.postChat(payload);
      }
    }
    if (response.status !== 'running') return response;
    return this.waitForChatCompletion(payload.idempotency_key, response);
  }

  getChatRequest(idempotencyKey: string): Promise<ChatResponse> {
    return this.authorized(
      `/api/mobile/v1/chat/requests/${encodeURIComponent(idempotencyKey)}`,
      { method: 'GET' },
      true,
      CHAT_STATUS_POLL_TIMEOUT_MS,
    );
  }

  private postChat(payload: ChatPayload): Promise<ChatResponse> {
    return this.authorized('/api/mobile/v1/chat/messages', {
      method: 'POST',
      body: JSON.stringify(payload),
    }, true, CHAT_REQUEST_TIMEOUT_MS);
  }

  private isUncertainChatOutcome(error: unknown): boolean {
    return error instanceof ApiError && (
      error.status === 0 || error.status === 502 || error.status === 503 || error.status === 504
    );
  }

  private async waitForChatCompletion(
    idempotencyKey: string,
    initial: ChatResponse,
  ): Promise<ChatResponse> {
    const deadline = Date.now() + CHAT_STATUS_POLL_WINDOW_MS;
    let response = initial;
    let delayMs = 1_000;
    while (response.status === 'running' && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, delayMs));
      response = await this.getChatRequest(idempotencyKey);
      delayMs = Math.min(5_000, Math.round(delayMs * 1.5));
    }
    return response;
  }

  updateProfile(nickname: string | null): Promise<MobileUser> {
    return this.authorized('/api/mobile/v1/me', {
      method: 'PATCH',
      body: JSON.stringify({ nickname }),
    });
  }

  saveCoachProfile(profile: CoachProfileInput): Promise<CoachProfileStatus> {
    return this.authorized('/api/mobile/v1/coach-profile', {
      method: 'PUT',
      body: JSON.stringify(profile),
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

  registerDevice(device: DeviceRegistration): Promise<void> {
    return this.authorized('/api/mobile/v1/devices/current', {
      method: 'PUT',
      body: JSON.stringify(device),
    });
  }

  createWeComBinding(): Promise<WeComBinding> {
    return this.authorized('/api/mobile/v1/bindings/wecom', { method: 'POST' });
  }

  getWeComBinding(): Promise<WeComBinding | null> {
    return this.authorized('/api/mobile/v1/bindings/wecom');
  }

  async deleteAccount(): Promise<void> {
    await this.authorized<void>('/api/mobile/v1/me', {
      method: 'DELETE',
      body: JSON.stringify({ confirmation: 'DELETE' }),
    });
    await this.clearSession();
  }

  private async authorized<T>(
    path: string,
    init: RequestInit = {},
    retry = true,
    timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
  ): Promise<T> {
    if (!this.accessToken) throw new ApiError(401, 'authentication_required', '请先登录');
    try {
      return await this.raw<T>(path, init, this.accessToken, timeoutMs);
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 401 || !retry) throw error;
      const refreshToken = await readRefreshToken();
      if (!refreshToken) {
        await this.clearSession();
        throw error;
      }
      await this.refresh(refreshToken);
      return this.authorized<T>(path, init, false, timeoutMs);
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

  private async raw<T>(
    path: string,
    init: RequestInit,
    bearer?: string,
    timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
  ): Promise<T> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
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
      connectionMonitor.response(response.status);
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
      try { return (await response.json()) as T; }
      catch { throw new ApiError(502, 'invalid_response', '服务器返回格式异常，请稍后再试'); }
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (error instanceof Error && error.name === 'AbortError') {
        throw new ApiError(0, 'request_timeout', '网络有点慢，请稍后再试');
      }
      connectionMonitor.failure('network_unavailable');
      throw new ApiError(0, 'network_unavailable', '暂时连不上服务器');
    } finally {
      clearTimeout(timeout);
    }
  }
}

export const mobileApi = new MobileApi();
