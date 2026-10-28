import { mobileApi } from '../../lib/api';
import type { PeriodDays, PeriodProgress } from './types';

export const progressApi = {
  period(days: PeriodDays): Promise<PeriodProgress> {
    return mobileApi.request(`/api/mobile/v1/progress/period?days=${days}`);
  },
};
