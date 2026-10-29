import { Ionicons } from '@expo/vector-icons';
import React from 'react';
import { StyleSheet, Switch, Text, TextInput, View } from 'react-native';

import { useApp } from '../../context/AppContext';
import { DetailPage } from '../../shared/ui/DetailPage';
import { appearance as palette } from '../../shared/ui/appearance';
import { SettingsAction, SettingsPanel, settingsStyles } from '../../shared/ui/SettingsPrimitives';
import type { Routine } from '../../types';
import { reminders, useReminderForm } from './useReminderForm';

export function ReminderScreen({ onClose }: { onClose: () => void }) {
  const { data, updateRoutine } = useApp();
  if (!data) return null;
  return <ReminderForm initial={data.routine} update={updateRoutine} onClose={onClose} />;
}

function ReminderForm({ initial, update, onClose }: { initial: Routine; update: (routine: Routine) => Promise<boolean>; onClose: () => void }) {
  const form = useReminderForm(initial, update, onClose);
  return <DetailPage title="日常提醒" onClose={form.close} busy={form.saving} footer={<SettingsAction title="保存提醒" busy={form.saving} onPress={() => void form.save()} />}>
    <Text style={settingsStyles.copy}>按你的节奏安排，提醒可以随时调整。</Text>
    <SettingsPanel>
      {reminders.map((item, index) => <View key={item.key} style={[styles.row, index > 0 && styles.divider]}>
        <View style={[styles.icon, { backgroundColor: palette[item.tone] }]}><Ionicons name={item.icon} size={24} color={item.tone === 'lavender' ? palette.ink : palette.orange} /></View>
        <View style={styles.copy}><Text style={styles.title}>{item.title}</Text>
          {form.routine[item.key] === null ? <Text style={settingsStyles.copy}>未开启</Text> : <TextInput accessibilityLabel={`${item.title}时间`} value={form.routine[item.key]!}
            onChangeText={value => form.change(item.key, value)} editable={!form.saving} keyboardType="numbers-and-punctuation" maxLength={5} style={styles.time} />}
        </View>
        <Switch accessibilityLabel={item.title} disabled={form.saving} value={form.routine[item.key] !== null} onValueChange={value => form.toggle(item.key, value)}
          trackColor={{ false: '#DDDEE5', true: palette.indigo }} thumbColor={palette.surface} />
      </View>)}
    </SettingsPanel>
    {form.error && <Text accessibilityRole="alert" style={settingsStyles.error}>{form.error}</Text>}
    {form.notice && <Text accessibilityLiveRegion="polite" style={settingsStyles.copy}>{form.notice}</Text>}
  </DetailPage>;
}

const styles = StyleSheet.create({
  row: { minHeight: 76, flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 10 }, divider: { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: palette.line },
  icon: { width: 44, height: 44, borderRadius: 14, justifyContent: 'center', alignItems: 'center' }, copy: { flex: 1 },
  title: { color: palette.ink, fontSize: 15, lineHeight: 22, fontWeight: '600' }, time: { color: palette.muted, fontSize: 15, paddingVertical: 5, minHeight: 40 },
});
