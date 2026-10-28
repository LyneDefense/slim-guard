export type RecordKind = 'meal' | 'exercise';
export type HistoryRecord = {
  ref: { kind: RecordKind; record_id: string; revision: number };
  occurrence: { precision: 'datetime' | 'date' | 'unknown'; at: string | null; on: string | null; timezone: string };
  content: { kind: 'meal'; meal_type: MealType; foods: { item_id: string; name: string | null; portion: string | null }[]; photo_asset_ids?: string[] }
    | { kind: 'exercise'; activity_name: string; duration_minutes: number | null; steps: number | null };
  recognition_issues: { issue_id: string; user_message: string }[];
};
export type RecordPage = { records: HistoryRecord[]; has_more: boolean; next_cursor: string | null };
export type MealType = 'breakfast' | 'lunch' | 'dinner' | 'snack' | 'unspecified';
export type MealEdit = {
  operation_id: string;
  expected_revision: number;
  meal_type: MealType;
  foods: { item_id: string | null; name: string }[];
};
export const mealLabels: Record<MealType, string> = {
  breakfast: '早餐', lunch: '午餐', dinner: '晚餐', snack: '加餐', unspecified: '其他',
};
