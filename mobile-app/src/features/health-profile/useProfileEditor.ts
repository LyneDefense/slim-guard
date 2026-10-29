import { useRef, useState } from 'react';
import { Alert } from 'react-native';

import type { CoachProfileData, CoachProfileInput, CoachProfileStatus } from '../../types';
import { healthSignature, profileForm, validateHealthForm } from './form';
import type { HealthForm } from './form';

export function useProfileEditor({ profile, nickname, intent, updateProfile, saveCoachProfile, onClose, onReady }: {
  profile: CoachProfileData | null; nickname: string | null; intent: 'edit' | 'coach';
  updateProfile: (nickname: string) => Promise<void>; saveCoachProfile: (input: CoachProfileInput) => Promise<CoachProfileStatus>; onClose: () => void; onReady: () => void;
}) {
  const [form, setForm] = useState(() => profileForm(profile, nickname));
  const baseline = useRef(form);
  const healthSaved = useRef(!!profile);
  const locked = useRef(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [attempted, setAttempted] = useState(false);
  const validation = validateHealthForm(form);
  const healthChanged = healthSignature(form) !== healthSignature(baseline.current);
  const nameChanged = form.nickname.trim() !== baseline.current.nickname.trim();
  const mustSaveHealth = healthChanged || (!healthSaved.current && (intent === 'coach' || !nameChanged));
  const dirty = healthChanged || nameChanged;
  function change<K extends keyof HealthForm>(key: K, value: HealthForm[K]) {
    if (locked.current) return;
    setForm(previous => ({ ...previous, [key]: value })); setError(null);
  }
  function requestClose() {
    if (locked.current) return;
    if (!dirty) { onClose(); return; }
    Alert.alert('放弃修改？', '修改尚未保存，是否放弃？', [
      { text: '继续修改', style: 'cancel' }, { text: '放弃修改', style: 'destructive', onPress: onClose },
    ]);
  }
  async function save() {
    if (locked.current) return;
    setAttempted(true);
    if (mustSaveHealth && !validation.payload) return;
    if (!dirty && healthSaved.current) { intent === 'coach' ? onReady() : onClose(); return; }
    locked.current = true; setSaving(true); setError(null);
    let accepted = false;
    try {
      if (mustSaveHealth && validation.payload) {
        const result = await saveCoachProfile(validation.payload);
        if (!result.profile || !result.coach_enabled) throw new Error('健康档案尚未确认保存，请重试');
        healthSaved.current = true;
        baseline.current = { ...form, nickname: baseline.current.nickname };
        accepted = true;
      }
      if (nameChanged) {
        await updateProfile(form.nickname.trim());
        baseline.current = { ...baseline.current, nickname: form.nickname.trim() };
      }
      intent === 'coach' ? onReady() : onClose();
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : '请稍后重试';
      setError(`${accepted ? '健康档案已保存，称呼未保存。' : '没有保存成功。'}${message}`);
    } finally { locked.current = false; setSaving(false); }
  }
  return { form, change, saving, dirty, error: error || (attempted && mustSaveHealth ? validation.error : null), requestClose, save };
}
