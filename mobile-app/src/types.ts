export type MobileUser = {
  id: string;
  nickname: string | null;
  identity_hint: string | null;
  created_at: string;
};

export type CoachAgeBand =
  | '0_9'
  | '10_17'
  | '18_29'
  | '30_39'
  | '40_49'
  | '50_59'
  | '60_69'
  | '70_79'
  | '80_plus';

export type CoachGoalType = 'lose_weight' | 'maintain_weight' | 'improve_habits';

export type CoachExerciseFrequency =
  | 'rarely'
  | 'weekly_1_2'
  | 'weekly_3_4'
  | 'weekly_5_plus';

export type CoachProfileInput = {
  age_band: CoachAgeBand;
  height_cm: number;
  current_weight_kg: number;
  weight_measured_on: string;
  goal_type: CoachGoalType;
  target_weight_kg: number;
  target_date: string;
  current_body_fat_percent: number | null;
  target_body_fat_percent: number | null;
  exercise_frequency: CoachExerciseFrequency | null;
};

export type CoachProfileData = CoachProfileInput & {
  revision: number;
  completed_at: string;
  updated_at: string;
};

export type CoachProfileStatus = {
  schema_version: 1;
  status: 'required' | 'ready' | 'unsupported_minor';
  coach_enabled: boolean;
  profile: CoachProfileData | null;
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

export type TestAccountOption = {
  username: string;
  default_nickname: string;
};

export type AuthOptions = {
  phone_login_enabled: boolean;
  test_account_login_enabled: boolean;
  test_accounts: TestAccountOption[];
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
  coach_profile: CoachProfileStatus;
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
