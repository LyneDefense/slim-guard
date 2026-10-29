import { mobileApi } from '../../lib/api';
import type { HistoryRecord, MealEdit, RecordKind, RecordPage } from './types';

export const recordHistoryApi = {
  list(kind: RecordKind, timezone: string, cursor?: string): Promise<RecordPage> {
    // The home preview shows two entries per section, then follows the server cursor.
    const query = new URLSearchParams({ kind, timezone, limit: '2' });
    if (cursor) query.set('cursor', cursor);
    return mobileApi.request(`/api/mobile/v1/records?${query}`);
  },
  meal(id: string): Promise<HistoryRecord> {
    return mobileApi.request(`/api/mobile/v1/records/meal/${encodeURIComponent(id)}`);
  },
  editMeal(id: string, edit: MealEdit): Promise<HistoryRecord> {
    return mobileApi.request(`/api/mobile/v1/records/meal/${encodeURIComponent(id)}`, {
      method: 'PATCH', body: JSON.stringify(edit),
    });
  },
};
