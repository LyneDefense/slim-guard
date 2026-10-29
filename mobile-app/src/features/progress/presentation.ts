import type { DailyMeasurement, MeasurementMetric } from './types';

export const measurementLabels = {
  weight: { label: '体重', unit: 'kg', draft: '我刚量了体重：' },
  bodyFat: { label: '体脂', unit: '%', draft: '我刚测了体脂：' },
} as const;

// API dates are calendar dates in the user's timezone. UTC is only used for
// calendar arithmetic, never to invent a measurement timestamp or local midnight.
function calendarDate(on: string): Date | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(on)) return null;
  const date = new Date(`${on}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === on ? date : null;
}

export function periodDates(from: string, to: string): string[] {
  const first = calendarDate(from), last = calendarDate(to);
  if (!first || !last || first > last) return [];
  const dates: string[] = [];
  for (const day = new Date(first); day <= last; day.setUTCDate(day.getUTCDate() + 1)) dates.push(day.toISOString().slice(0, 10));
  return dates;
}

export function measurementDates(points: DailyMeasurement[], from: string, to: string): DailyMeasurement[] {
  return points.filter(point => point.on >= from && point.on <= to && calendarDate(point.on) && Number.isFinite(point.value))
    .slice().sort((a, b) => a.on.localeCompare(b.on));
}

export function shortDate(on: string): string {
  return calendarDate(on) ? on.slice(5).replace('-', '/') : '日期未明确';
}

export function weekday(on: string): string {
  const date = calendarDate(on);
  return date ? ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][date.getUTCDay()] : '';
}

export function calendarCaption(from: string, to: string): string {
  if (!calendarDate(from) || !calendarDate(to)) return '有测量的日子';
  const first = Number(from.slice(5, 7)), last = Number(to.slice(5, 7));
  const months = from.slice(0, 4) !== to.slice(0, 4)
    ? `${from.slice(0, 4)}/${first} — ${to.slice(0, 4)}/${last}`
    : first === last ? `${first}月` : `${first} — ${last}月`;
  return `${months} · 有测量的日子`;
}

export function periodChange(points: DailyMeasurement[], metric: MeasurementMetric): string {
  if (!points.length) return `暂无${measurementLabels[metric].label}记录`;
  if (points.length === 1) return '仅有一天测量';
  const delta = Number((points[points.length - 1].value - points[0].value).toFixed(1));
  const sign = delta < 0 ? '−' : delta > 0 ? '+' : '';
  return `${sign}${Math.abs(delta).toFixed(1)} ${metric === 'weight' ? 'kg' : '个百分点'}`;
}

export function datedMeasurementDraft(metric: MeasurementMetric, on: string): string {
  return `我想记录 ${on} 的${measurementLabels[metric].label}：`;
}
