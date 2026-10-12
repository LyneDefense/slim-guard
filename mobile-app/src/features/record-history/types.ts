export type RecordKind = 'meal' | 'exercise';
export type HistoryRecord = {
  ref: { kind: RecordKind; record_id: string; revision: number };
  occurrence: { precision: 'datetime' | 'date' | 'unknown'; at: string | null; on: string | null; timezone: string };
  content: { kind: 'meal'; meal_type: string; foods: { item_id: string; name: string | null; portion: string | null }[] }
    | { kind: 'exercise'; activity_name: string; duration_minutes: number | null; steps: number | null };
  recognition_issues: { issue_id: string; user_message: string }[];
};
export type RecordPage = { records: HistoryRecord[]; has_more: boolean; next_cursor: string | null };
