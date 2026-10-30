import { mobileApi } from '../../lib/api';
import type { DailyInsights } from './types';

export const dailyInsightsApi = {
  get: () => mobileApi.request<DailyInsights>('/api/mobile/v1/daily-insights'),
  retry: () => mobileApi.request<DailyInsights>('/api/mobile/v1/daily-insights/retry', { method: 'POST' }),
};
