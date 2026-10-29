import { useRef, useState } from 'react';
import { Alert } from 'react-native';

import type { Routine } from '../../types';

export const reminders = [
  { key: 'weight_reminder_time', title: '称重提醒', fallback: '08:00', icon: 'scale-outline', tone: 'gold' },
  { key: 'meal_reminder_time', title: '饮食记录', fallback: '12:30', icon: 'restaurant-outline', tone: 'lavender' },
  { key: 'daily_review_time', title: '晚间复盘', fallback: '21:30', icon: 'moon-outline', tone: 'peach' },
] as const;
type ReminderKey = typeof reminders[number]['key'];

export function useReminderForm(initial: Routine, update: (routine: Routine) => Promise<boolean>, onClose: () => void) {
  const [routine, setRoutine] = useState(() => ({ ...initial }));
  const baseline = useRef(initial), remembered = useRef({ ...initial }), locked = useRef(false);
  const [saving, setSaving] = useState(false), [error, setError] = useState<string | null>(null), [notice, setNotice] = useState<string | null>(null);
  const dirty = JSON.stringify(routine) !== JSON.stringify(baseline.current);
  function change(key: ReminderKey, value: string | null) {
    if (locked.current) return;
    if (value !== null) remembered.current[key] = value;
    setRoutine(previous => ({ ...previous, [key]: value })); setError(null); setNotice(null);
  }
  function toggle(key: ReminderKey, enabled: boolean) {
    change(key, enabled ? remembered.current[key] || reminders.find(item => item.key === key)!.fallback : null);
  }
  function close() {
    if (locked.current) return;
    if (!dirty) { onClose(); return; }
    Alert.alert('放弃修改？', '提醒设置尚未保存，是否放弃？', [{ text: '继续修改', style: 'cancel' }, { text: '放弃修改', style: 'destructive', onPress: onClose }]);
  }
  async function save() {
    if (locked.current) return;
    if (reminders.some(item => routine[item.key] !== null && !/^([01]\d|2[0-3]):[0-5]\d$/.test(routine[item.key]!))) { setError('时间请用 24 小时制，例如 08:30 或 21:00。'); return; }
    locked.current = true; setSaving(true); setError(null);
    try {
      const allowed = await update(routine); baseline.current = { ...routine };
      setNotice(allowed ? '提醒已保存，会按设备本地时间提醒你。' : '设置已保存，但系统通知权限未开启，暂时不会弹提醒。');
    } catch (caught) { setError(caught instanceof Error ? caught.message : '没有保存成功，请重试'); }
    finally { locked.current = false; setSaving(false); }
  }
  return { routine, saving, error, notice, change, toggle, close, save };
}
