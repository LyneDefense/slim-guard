import type { MemoryItem } from '../types';

const labels: Record<string, string> = {
  'identity.preferred_name': '希望怎么称呼你',
  'profile.height': '身高',
  'profile.exercise_habit': '运动习惯',
  'coaching.response_style': '喜欢的交流方式',
  'food.preference': '饮食偏好',
  'exercise.preference': '运动偏好',
  'goal.target_weight': '目标体重',
  'goal.target_body_fat': '目标体脂',
  'goal.behavior': '行动目标',
  'constraint.dietary': '饮食限制',
  'constraint.exercise': '运动限制',
  'constraint.health_context': '健康背景',
};

const stance: Record<string, string> = { like: '喜欢', dislike: '不喜欢', avoid: '避免' };

export function memoryLabel(memory: MemoryItem): string {
  return labels[memory.key] || memory.key;
}

export function memoryText(memory: MemoryItem): string {
  const value = memory.value;
  if (typeof value.name === 'string') return value.name;
  if (typeof value.millimeters === 'number') return `${value.millimeters / 10} cm`;
  if (typeof value.grams === 'number') {
    const suffix = typeof value.target_date === 'string' ? ` · ${value.target_date} 前` : '';
    return `${value.grams / 1000} kg${suffix}`;
  }
  if (typeof value.basis_points === 'number') return `${value.basis_points / 100}%`;
  if (typeof value.statement === 'string') return value.statement;
  if (typeof value.item === 'string') return `${stance[String(value.stance)] || ''}${value.item}`;
  if (typeof value.activity === 'string') return `${stance[String(value.stance)] || ''}${value.activity}`;
  if (typeof value.target === 'number') {
    const units: Record<string, string> = {
      weekly_exercise_sessions: '次运动/周',
      daily_steps: '步/天',
      daily_meal_checkins: '次饮食记录/天',
    };
    return `${value.target} ${units[String(value.kind)] || ''}`.trim();
  }
  return Object.values(value).filter((item) => typeof item === 'string' || typeof item === 'number').join(' · ');
}

export function findGoal(memories: MemoryItem[], key: string): string | null {
  const memory = memories.find((item) => item.key === key && !item.stale);
  return memory ? memoryText(memory) : null;
}
