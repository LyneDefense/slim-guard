import { Ionicons } from '@expo/vector-icons';
import React from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';
import type { ViewStyle } from 'react-native';

import { appearance as palette } from './appearance';

export function SettingsPanel({ children, style }: { children: React.ReactNode; style?: ViewStyle }) {
  return <View style={[styles.panel, style]}>{children}</View>;
}

export function PanelHeading({ title, icon, tone = 'lavender' }: {
  title: string; icon: keyof typeof Ionicons.glyphMap; tone?: 'lavender' | 'peach' | 'gold' | 'blue';
}) {
  return <View style={styles.heading}>
    <View style={[styles.icon, { backgroundColor: palette[tone] }]}><Ionicons name={icon} size={24} color={tone === 'peach' || tone === 'gold' ? palette.orange : palette.ink} /></View>
    <Text accessibilityRole="header" style={styles.headingText}>{title}</Text>
  </View>;
}

export function SettingsAction({ title, onPress, disabled = false, busy = false, variant = 'primary', icon }: {
  title: string; onPress: () => void; disabled?: boolean; busy?: boolean; variant?: 'primary' | 'secondary' | 'danger'; icon?: keyof typeof Ionicons.glyphMap;
}) {
  const foreground = variant === 'primary' ? palette.surface : variant === 'danger' ? '#BA4A45' : palette.ink;
  return <Pressable accessibilityRole="button" accessibilityLabel={title} accessibilityState={{ disabled: disabled || busy, busy }}
    disabled={disabled || busy} onPress={onPress} style={({ pressed }) => [styles.button, styles[variant], (disabled || busy) && styles.disabled, pressed && styles.pressed]}>
    {busy ? <ActivityIndicator color={foreground} /> : <>
      {icon && <Ionicons name={icon} size={21} color={foreground} />}<Text style={[styles.buttonText, { color: foreground }]}>{title}</Text>
    </>}
  </Pressable>;
}

export const settingsStyles = StyleSheet.create({
  copy: { color: palette.muted, fontSize: 13, lineHeight: 21 },
  label: { color: palette.muted, fontSize: 12, lineHeight: 18, marginBottom: 5 },
  input: { minHeight: 44, borderWidth: 1, borderColor: '#E5E6FA', backgroundColor: '#F6F5FE', color: palette.ink, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 9, fontSize: 16 },
  error: { color: '#BA4A45', fontSize: 13, lineHeight: 21 },
  actions: { flexDirection: 'row', gap: 10 }, sectionTitle: { color: palette.ink, fontSize: 19, lineHeight: 27, fontWeight: '700' },
});

const styles = StyleSheet.create({
  panel: { ...palette.shadow, backgroundColor: palette.surface, borderRadius: 18, padding: 14, gap: 12 },
  heading: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  icon: { width: 42, height: 42, borderRadius: 13, alignItems: 'center', justifyContent: 'center' },
  headingText: { fontSize: 18, lineHeight: 25, color: palette.ink, fontWeight: '700' },
  button: { minHeight: 48, paddingHorizontal: 14, paddingVertical: 11, borderRadius: 14, alignItems: 'center', justifyContent: 'center', flexDirection: 'row', gap: 10 },
  primary: { backgroundColor: palette.indigo }, secondary: { backgroundColor: palette.lavender }, danger: { backgroundColor: '#FFF0EB' },
  buttonText: { fontSize: 16, lineHeight: 23, fontWeight: '700' }, disabled: { opacity: 0.45 }, pressed: { opacity: 0.75 },
});
