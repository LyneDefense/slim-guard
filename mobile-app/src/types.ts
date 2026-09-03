export type MobileUser = {
  id: string;
  nickname: string | null;
  identity_hint: string | null;
  created_at: string;
};

export type AuthTokens = {
  token_type: 'Bearer';
  access_token: string;
  expires_in_seconds: number;
  refresh_token: string;
  user: MobileUser;
};

export type OtpChallenge = {
  challenge_id: string;
  expires_in_seconds: number;
  retry_after_seconds: number;
  debug_code: string | null;
};

export type ChatPayload = {
  text?: string;
  image_base64?: string;
  image_mime_type?: 'image/jpeg' | 'image/png' | 'image/webp';
  idempotency_key: string;
  occurred_at: string;
};

export type ChatResponse = {
  request_id: string;
  status: 'running' | 'succeeded' | 'failed';
  turn_id: string | null;
  text: string | null;
  failure_code: string | null;
  replayed: boolean;
};

export type ChatMessage = {
  id: string;
  turn_id: string;
  role: 'user' | 'assistant';
  kind: 'text' | 'image';
  text: string | null;
  created_at: string;
  pending?: boolean;
  failed?: boolean;
};

export type MemoryItem = {
  id: string;
  key: string;
  kind: string;
  value: Record<string, unknown>;
  stale: boolean;
  valid_from: string;
  review_after: string | null;
};

export type Routine = {
  timezone: string;
  weight_reminder_time: string | null;
  meal_reminder_time: string | null;
  daily_review_time: string | null;
};

export type Today = {
  date: string;
  current_weight_kg: number | null;
  current_body_fat_percent: number | null;
  meals_logged: number;
  exercise_logged: number;
  memories: MemoryItem[];
  routine: Routine;
};

export type TrendPoint = { id: string; value: number; occurred_at: string };

export type Progress = {
  weights: TrendPoint[];
  body_fat: TrendPoint[];
  meals: Array<Record<string, unknown>>;
  exercise: Array<Record<string, unknown>>;
};

export type DashboardData = {
  user: MobileUser;
  today: Today;
  progress: Progress;
  memories: MemoryItem[];
  routine: Routine;
  messages: ChatMessage[];
};

export type PendingChat = {
  payload: Omit<ChatPayload, 'image_base64'>;
  imageUri?: string;
  previewUri?: string;
  createdAt: string;
};

export type DeviceRegistration = {
  installation_id: string;
  platform: 'ios' | 'android';
  push_provider: 'expo';
  push_token: string;
  app_version: string | null;
  timezone: string;
  locale: string | null;
};

export type WeComBinding = {
  id: string;
  status: 'pending' | 'claimed' | 'expired' | 'revoked' | 'conflict';
  code: string | null;
  code_hint: string;
  expires_at: string;
  claimed_at: string | null;
};
