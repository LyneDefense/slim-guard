export type InsightEvidence = {
  id: string;
  kind: string;
  label: string;
  today: boolean;
  data: { content?: { duration_minutes?: number | null; steps?: number | null }; [key: string]: unknown };
};

export type DailyInsights = {
  date: string;
  timezone: string;
  status: 'pending' | 'running' | 'ready' | 'failed' | 'unavailable';
  headline: string | null;
  summary: string | null;
  summary_state: 'empty' | 'pending' | 'ready' | 'failed' | 'unavailable';
  as_of: string | null;
  generated_at: string | null;
  evidence: InsightEvidence[];
  failure_code: string | null;
};
