import React from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { useApp } from '../context/AppContext';
import { HealthProfileFields } from '../features/health-profile/HealthProfileFields';
import { useProfileEditor } from '../features/health-profile/useProfileEditor';
import { DetailPage } from '../shared/ui/DetailPage';
import { SettingsAction, settingsStyles } from '../shared/ui/SettingsPrimitives';

export function CoachProfileScreen({ intent, onClose, onReady }: {
  intent: 'coach' | 'edit'; onClose: () => void; onReady: () => void;
}) {
  const { data, saveCoachProfile, updateProfile } = useApp();
  const editor = useProfileEditor({ profile: data?.coach_profile.profile ?? null, nickname: data?.user.nickname ?? null,
    intent, saveCoachProfile, updateProfile, onClose, onReady });
  if (!data) return null;
  return <DetailPage title={intent === 'edit' ? '编辑健康档案' : '健康档案'} centeredTitle onClose={editor.requestClose} busy={editor.saving}
    footer={<View style={settingsStyles.actions}>
      <View style={styles.cancel}><SettingsAction title="取消" variant="secondary" disabled={editor.saving} onPress={editor.requestClose} /></View>
      <View style={styles.save}><SettingsAction title={intent === 'coach' ? '保存并进入教练' : '保存修改'} busy={editor.saving} onPress={() => void editor.save()} /></View>
    </View>}>
    <Text style={settingsStyles.copy}>{intent === 'coach' ? '填写必要资料后，教练才能了解你的当前状态与目标。' : '让教练了解现在的你。'}</Text>
    <HealthProfileFields form={editor.form} change={editor.change} saving={editor.saving} />
    {editor.error && <Text accessibilityRole="alert" style={settingsStyles.error}>{editor.error}</Text>}
    <Text style={styles.footnote}>目标由你设定，可随时调整。</Text>
  </DetailPage>;
}

const styles = StyleSheet.create({
  cancel: { flex: 1 }, save: { flex: 1.7 }, footnote: { ...settingsStyles.copy, textAlign: 'center', fontSize: 11 },
});
