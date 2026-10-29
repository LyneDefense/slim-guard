import { Ionicons } from '@expo/vector-icons';
import React, { useState } from 'react';
import { Pressable, StyleSheet, Text, TextInput, View } from 'react-native';

import { DateField } from '../../components/DateField';
import { startOfToday, tomorrow } from '../../lib/dates';
import { ChoicePicker } from '../../shared/ui/ChoicePicker';
import { appearance as palette } from '../../shared/ui/appearance';
import { PanelHeading, SettingsPanel, settingsStyles } from '../../shared/ui/SettingsPrimitives';
import { ageOptions, exerciseOptions, goalOptions, sanitizeNumber } from './form';
import type { HealthForm } from './form';

type Props = { form: HealthForm; change: <K extends keyof HealthForm>(key: K, value: HealthForm[K]) => void; saving: boolean };

export function HealthProfileFields({ form, change, saving }: Props) {
  const [supplement, setSupplement] = useState(false);
  return <>
    <SettingsPanel>
      <PanelHeading title="基本资料" icon="person-outline" tone="peach" />
      <View><Text style={settingsStyles.label}>称呼</Text><TextInput accessibilityLabel="称呼" value={form.nickname} editable={!saving}
        maxLength={80} onChangeText={value => change('nickname', value)} placeholder="你希望怎么称呼" placeholderTextColor={palette.muted} style={settingsStyles.input} /></View>
      <View style={styles.pair}>
        <View style={styles.column}><ChoicePicker label="年龄段" value={form.ageBand} options={ageOptions} disabled={saving} onChange={value => change('ageBand', value)} /></View>
        <View style={styles.column}><NumberField label="身高" unit="cm" value={form.height} disabled={saving} onChange={value => change('height', value)} /></View>
      </View>
    </SettingsPanel>
    <SettingsPanel>
      <PanelHeading title="体重与目标" icon="scale-outline" />
      <View><Text style={settingsStyles.label}>使用目标</Text><View style={styles.goals}>
        {goalOptions.map(option => <Pressable key={option.value} accessibilityRole="radio" accessibilityLabel={option.label}
          accessibilityState={{ checked: form.goalType === option.value, disabled: saving }} disabled={saving} onPress={() => change('goalType', option.value)}
          style={[styles.goal, form.goalType === option.value && styles.goalSelected]}>
          <Ionicons name={form.goalType === option.value ? 'radio-button-on' : 'radio-button-off'} size={16} color={form.goalType === option.value ? palette.ink : palette.muted} />
          <Text style={styles.goalText}>{option.label}</Text>
        </Pressable>)}
      </View></View>
      <View style={styles.pair}>
        <View style={styles.column}><NumberField label="当前体重" unit="kg" value={form.currentWeight} disabled={saving} onChange={value => change('currentWeight', value)} /></View>
        <View style={styles.column}><NumberField label="目标体重" unit="kg" value={form.targetWeight} disabled={saving} onChange={value => change('targetWeight', value)} /></View>
      </View>
      <View style={styles.pair}>
        <View style={styles.column}><DateField compact disabled={saving} label="体重测量日期" value={form.weightDate} onChange={value => change('weightDate', value)} maximumDate={startOfToday()} /></View>
        <View style={styles.column}><DateField compact disabled={saving} label="目标日期" value={form.targetDate} onChange={value => change('targetDate', value)} minimumDate={tomorrow()} /></View>
      </View>
    </SettingsPanel>
    <SettingsPanel>
      <Pressable accessibilityRole="button" accessibilityLabel="补充信息" accessibilityState={{ expanded: supplement }} onPress={() => setSupplement(value => !value)} style={styles.disclosure}>
        <PanelHeading title="补充信息" icon="document-text-outline" tone="gold" />
        <Text style={styles.optional}>选填</Text><Ionicons name={supplement ? 'chevron-up' : 'chevron-down'} size={18} color={palette.muted} />
      </Pressable>
      {supplement ? <>
        <View style={styles.pair}>
          <View style={styles.column}><NumberField label="当前体脂率" unit="%" value={form.currentBodyFat} disabled={saving} onChange={value => change('currentBodyFat', value)} /></View>
          <View style={styles.column}><NumberField label="目标体脂率" unit="%" value={form.targetBodyFat} disabled={saving} onChange={value => change('targetBodyFat', value)} /></View>
        </View>
        <ChoicePicker label="运动习惯" value={form.exercise} options={exerciseOptions} disabled={saving} allowClear onChange={value => change('exercise', value)} />
      </> : <Text style={settingsStyles.copy}>体脂率、运动习惯</Text>}
    </SettingsPanel>
  </>;
}

function NumberField({ label, unit, value, onChange, disabled }: { label: string; unit: string; value: string; onChange: (value: string) => void; disabled: boolean }) {
  return <View><Text style={settingsStyles.label}>{label}</Text><View style={styles.number}>
    <TextInput accessibilityLabel={label} value={value} onChangeText={text => onChange(sanitizeNumber(text))} keyboardType="decimal-pad" maxLength={6}
      editable={!disabled} placeholder="未填写" placeholderTextColor={palette.muted} style={styles.input} /><Text style={styles.unit}>{unit}</Text>
  </View></View>;
}

const styles = StyleSheet.create({
  pair: { flexDirection: 'row', gap: 12 }, column: { flex: 1, minWidth: 0 },
  number: { ...settingsStyles.input, flexDirection: 'row', alignItems: 'center', gap: 6, paddingVertical: 0, paddingHorizontal: 0 },
  input: { flex: 1, minWidth: 0, color: palette.ink, fontSize: 16, minHeight: 44, paddingLeft: 12, paddingVertical: 9 }, unit: { color: palette.muted, fontSize: 12, paddingRight: 12 },
  goals: { flexDirection: 'row', flexWrap: 'wrap', gap: 7 }, goal: { minHeight: 40, flex: 1, minWidth: 85, borderRadius: 22, borderWidth: 1, borderColor: '#E5E6FA', paddingHorizontal: 8, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6 },
  goalSelected: { backgroundColor: palette.lavender }, goalText: { color: palette.ink, fontSize: 12, fontWeight: '600' },
  disclosure: { flexDirection: 'row', alignItems: 'center', gap: 8, minHeight: 44 }, optional: { flex: 1, color: palette.muted, fontSize: 11, textAlign: 'right' },
});
