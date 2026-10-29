import { dateText, parseDate, todayText } from '../../lib/dates';
import type { CoachAgeBand, CoachExerciseFrequency, CoachGoalType, CoachProfileData, CoachProfileInput } from '../../types';

export const ageOptions: { value: CoachAgeBand; label: string }[] = [
  ['0_9', '0–9 岁'], ['10_17', '10–17 岁'], ['18_29', '18–29 岁'], ['30_39', '30–39 岁'], ['40_49', '40–49 岁'],
  ['50_59', '50–59 岁'], ['60_69', '60–69 岁'], ['70_79', '70–79 岁'], ['80_plus', '80 岁及以上'],
].map(([value, label]) => ({ value: value as CoachAgeBand, label }));
export const goalOptions: { value: CoachGoalType; label: string }[] = [
  { value: 'lose_weight', label: '减重' }, { value: 'maintain_weight', label: '维持体重' }, { value: 'improve_habits', label: '改善习惯' },
];
export const exerciseOptions: { value: CoachExerciseFrequency; label: string }[] = [
  { value: 'rarely', label: '基本不运动' }, { value: 'weekly_1_2', label: '每周 1–2 次' },
  { value: 'weekly_3_4', label: '每周 3–4 次' }, { value: 'weekly_5_plus', label: '每周 5 次及以上' },
];

export type HealthForm = {
  nickname: string; ageBand: CoachAgeBand | null; height: string; currentWeight: string; weightDate: string;
  goalType: CoachGoalType | null; targetWeight: string; targetDate: string; currentBodyFat: string; targetBodyFat: string; exercise: CoachExerciseFrequency | null;
};

export function profileForm(profile: CoachProfileData | null, nickname: string | null): HealthForm {
  const text = (value?: number | null) => value === null || value === undefined ? '' : String(value);
  return { nickname: nickname || '', ageBand: profile?.age_band ?? null, height: text(profile?.height_cm), currentWeight: text(profile?.current_weight_kg),
    weightDate: profile?.weight_measured_on || todayText(), goalType: profile?.goal_type ?? null, targetWeight: text(profile?.target_weight_kg), targetDate: profile?.target_date || '',
    currentBodyFat: text(profile?.current_body_fat_percent), targetBodyFat: text(profile?.target_body_fat_percent), exercise: profile?.exercise_frequency ?? null };
}

export function sanitizeNumber(value: string): string {
  const [whole = '', ...fractions] = value.replace(',', '.').replace(/[^0-9.]/g, '').split('.');
  return fractions.length ? `${whole}.${fractions.join('').slice(0, 1)}` : whole;
}

export function healthSignature(form: HealthForm): string {
  const { nickname: _nickname, ...health } = form;
  return JSON.stringify(health);
}

export function validateHealthForm(form: HealthForm, today = todayText()): { payload: CoachProfileInput | null; error: string | null } {
  const invalid = (error: string) => ({ payload: null, error });
  const number = (value: string) => /^\d+(?:\.\d)?$/.test(value) ? Number(value) : NaN;
  const date = (value: string) => /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(parseDate(value).getTime()) && dateText(parseDate(value)) === value;
  if (!form.ageBand || !form.goalType || !form.weightDate || !form.targetDate) return invalid('请填写全部必填项');
  const height = number(form.height), current = number(form.currentWeight), target = number(form.targetWeight);
  if (!Number.isFinite(height) || height < 50 || height > 250) return invalid('身高请输入 50–250 cm，最多一位小数');
  if (!Number.isFinite(current) || current < 10 || current > 500) return invalid('当前体重请输入 10–500 kg，最多一位小数');
  if (!Number.isFinite(target) || target < 10 || target > 500) return invalid('目标体重请输入 10–500 kg，最多一位小数');
  if (!date(form.weightDate) || form.weightDate > today) return invalid('体重测量日期不能晚于今天，请选择有效日期');
  if (!date(form.targetDate) || form.targetDate <= today || form.targetDate <= form.weightDate) return invalid('目标日期必须晚于今天和体重测量日期');
  if (form.goalType === 'lose_weight' && target >= current) return invalid('选择减重时，目标体重需要低于当前体重');
  const currentFat = form.currentBodyFat ? number(form.currentBodyFat) : null, targetFat = form.targetBodyFat ? number(form.targetBodyFat) : null;
  if (currentFat !== null && (!Number.isFinite(currentFat) || currentFat < 1 || currentFat > 75)) return invalid('当前体脂率请输入 1–75%，最多一位小数');
  if (targetFat !== null && (!Number.isFinite(targetFat) || targetFat < 1 || targetFat > 75)) return invalid('目标体脂率请输入 1–75%，最多一位小数');
  return { error: null, payload: { age_band: form.ageBand, height_cm: height, current_weight_kg: current, weight_measured_on: form.weightDate,
    goal_type: form.goalType, target_weight_kg: target, target_date: form.targetDate, current_body_fat_percent: currentFat, target_body_fat_percent: targetFat, exercise_frequency: form.exercise } };
}
