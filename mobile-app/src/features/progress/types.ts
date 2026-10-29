export type PeriodDays = 7 | 30;
export type MeasurementMetric = 'weight' | 'bodyFat';
export type DailyMeasurement = { record_id: string; on: string; value: number; unit: 'kg' | '%' };
export type PeriodProgress = {
  days: PeriodDays;
  from_date: string;
  to_date: string;
  timezone: string;
  counts: { weight: number; body_fat: number; meal: number; exercise: number };
  weights: DailyMeasurement[];
  body_fat: DailyMeasurement[];
};
