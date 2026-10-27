import type { HistoryRecord } from './types';

const mealLabels: Record<string, string> = {
  breakfast: '早餐',
  lunch: '午餐',
  dinner: '晚餐',
  snack: '加餐',
  unspecified: '餐食',
};

export function mealCorrectionDraft(record: HistoryRecord, timezone: string): string | undefined {
  if (record.content.kind !== 'meal') return undefined;

  const occurrence = record.occurrence.at
    ? new Date(record.occurrence.at).toLocaleString('zh-CN', {
        timeZone: timezone,
        year: 'numeric',
        month: 'numeric',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      })
    : record.occurrence.on || '日期未明确';
  const foods = record.content.foods
    .map((food) => `${food.name || '菜品未命名'}${food.portion ? `（${food.portion}）` : ''}`)
    .join('、') || '暂无菜品';

  return [
    '我想更正一条饮食记录。',
    `记录时间：${occurrence}`,
    `餐次：${mealLabels[record.content.meal_type] || '饮食'}`,
    `当前记录的菜品：${foods}`,
    '请帮我更正为：',
  ].join('\n');
}
