import { memoryLabel, memoryText } from '../../lib/memoryFormat';
import type { MemoryItem } from '../../types';

export type MemoryGroup = 'goal' | 'preference' | 'constraint' | 'other';
export const memoryGroups: { id: MemoryGroup; tab: string; title: string }[] = [
  { id: 'goal', tab: '目标', title: '你的目标' }, { id: 'preference', tab: '习惯偏好', title: '习惯与偏好' },
  { id: 'constraint', tab: '限制', title: '需要留意' }, { id: 'other', tab: '其他', title: '其他资料' },
];

export function memoryGroup(memory: MemoryItem): MemoryGroup {
  if (memory.kind === 'goal') return 'goal';
  if (memory.kind === 'constraint') return 'constraint';
  if (['food.preference', 'exercise.preference', 'profile.exercise_habit', 'coaching.response_style'].includes(memory.key)) return 'preference';
  return 'other';
}

export function memoryPresentation(memory: MemoryItem) {
  const rawLabel = memoryLabel(memory);
  const label = rawLabel === memory.key ? '其他信息' : rawLabel;
  const weightGoal = memory.key === 'goal.target_weight' && typeof memory.value.grams === 'number';
  return {
    label, value: weightGoal ? `${Number(memory.value.grams) / 1000} kg` : memoryText(memory) || '暂无可展示的内容',
    detail: weightGoal && typeof memory.value.target_date === 'string' ? `希望在 ${memory.value.target_date.replaceAll('-', '/')} 前达到` : null,
    emphasized: weightGoal,
    icon: memoryGroup(memory) === 'goal' ? 'flag-outline' : memoryGroup(memory) === 'constraint' ? 'shield-checkmark-outline'
      : memory.key.startsWith('food.') ? 'restaurant-outline' : memory.key.startsWith('exercise.') || memory.key === 'profile.exercise_habit' ? 'walk-outline' : 'document-text-outline',
    tone: memoryGroup(memory) === 'goal' ? 'gold' : memoryGroup(memory) === 'constraint' ? 'peach' : memory.key.startsWith('exercise.') ? 'blue' : 'lavender',
  } as const;
}
