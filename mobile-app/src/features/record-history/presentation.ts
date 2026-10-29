import type { HistoryRecord, MealType } from './types';

// Date-only observations must not acquire an invented time or shift across time zones.
export function recordDate(occurrence: HistoryRecord['occurrence'], timezone: string, withTime: boolean): string {
  if (occurrence.at) {
    const date = new Date(occurrence.at);
    if (!Number.isNaN(date.getTime())) {
      const parts = new Intl.DateTimeFormat('en-GB', {
        timeZone: timezone, month: 'numeric', day: 'numeric',
        ...(withTime && occurrence.precision === 'datetime' ? { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' as const } : {}),
      }).formatToParts(date);
      const part = (name: string) => parts.find(item => item.type === name)?.value;
      return `${part('month')}月${part('day')}日${part('hour') ? ` ${part('hour')}:${part('minute')}` : ''}`;
    }
  }
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(occurrence.on || '');
  return dateOnly ? `${Number(dateOnly[2])}月${Number(dateOnly[3])}日` : '日期未明确';
}

export const mealBadgeColors: Record<MealType, { backgroundColor: string; color: string }> = {
  breakfast: { backgroundColor: '#FFF2D4', color: '#B96709' },
  lunch: { backgroundColor: '#FFEADC', color: '#C35413' },
  dinner: { backgroundColor: '#EEEFFC', color: '#4B588E' },
  snack: { backgroundColor: '#E5F1F8', color: '#386682' },
  unspecified: { backgroundColor: '#F0EFEC', color: '#6F6C67' },
};
