import { Ionicons } from '@expo/vector-icons';
import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { AppIcon } from '../../shared/ui/AppIcon';
import type { AppIconName } from '../../shared/ui/AppIcon';
import { appearance as palette } from '../../shared/ui/appearance';
import { TonalBackdrop } from '../../shared/ui/TonalBackdrop';

export function MetricCard({ label, value, unit, target, tone, onPress }: {
  label: string; value: string; unit: string; target: string; tone: 'indigo' | 'apricot'; onPress: () => void;
}) {
  const indigo = tone === 'indigo';
  const foreground = { color: indigo ? palette.surface : palette.orangeInk };
  return <Pressable accessibilityRole="button" accessibilityLabel={`${label} ${value} ${unit}，${target}`} onPress={onPress}
    style={[styles.metric, { backgroundColor: indigo ? palette.indigo : palette.apricot }]}>
    <TonalBackdrop tone={tone} />
    <Text style={[styles.metricLabel, foreground]}>{label}</Text>
    <View style={styles.valueRow}><Text adjustsFontSizeToFit minimumFontScale={0.65} numberOfLines={1} style={[styles.value, foreground]}>{value}</Text><Text style={[styles.unit, foreground]}>{unit}</Text></View>
    <Text style={[styles.target, foreground]}>{target}</Text>
  </Pressable>;
}

const actions: { icon: AppIconName; title: string; subtitle: string; background: string; color: string; draft?: string }[] = [
  { icon: 'scale', title: '记体重', subtitle: '看长期趋势', background: palette.peach, color: palette.orange, draft: '我刚量了体重：' },
  { icon: 'meal', title: '记饮食', subtitle: '文字或拍照', background: palette.lavender, color: palette.indigo, draft: '我刚吃了' },
  { icon: 'exercise', title: '记运动', subtitle: '做了什么都算', background: palette.gold, color: palette.orange, draft: '我刚运动了' },
  { icon: 'chat', title: '聊一聊', subtitle: '问教练任何事', background: palette.blue, color: palette.indigo },
];

export function TodayActions({ openCoach }: { openCoach: (draft?: string) => void }) {
  return <View style={styles.grid}>
    {actions.map(action => <Pressable key={action.title} accessibilityRole="button" accessibilityLabel={action.title}
      onPress={() => openCoach(action.draft)} style={({ pressed }) => [styles.action, pressed && styles.pressed]}>
      <View style={[styles.tile, { backgroundColor: action.background }]}><AppIcon name={action.icon} color={action.color} size={28} /></View>
      <View style={styles.actionCopy}><Text style={styles.actionTitle}>{action.title}</Text><Text style={styles.subtitle}>{action.subtitle}</Text></View>
      <Ionicons name="chevron-forward" size={16} color={palette.muted} />
    </Pressable>)}
  </View>;
}

const styles = StyleSheet.create({
  metric: { flex: 1, minWidth: 0, minHeight: 138, borderRadius: 18, padding: 15, overflow: 'hidden' },
  metricLabel: { fontSize: 13, lineHeight: 19 },
  valueRow: { flexDirection: 'row', alignItems: 'baseline', marginTop: 10, marginBottom: 8 },
  value: { fontSize: 44, lineHeight: 53, fontWeight: '700', letterSpacing: -1.5, flexShrink: 1 },
  unit: { fontSize: 16, fontWeight: '600', marginLeft: 5 },
  target: { fontSize: 13, lineHeight: 19, marginTop: 'auto' },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 12 },
  action: { ...palette.shadow, width: '48%', flexGrow: 1, backgroundColor: palette.surface, borderRadius: 16, padding: 10, minHeight: 67, flexDirection: 'row', alignItems: 'center', gap: 10 },
  pressed: { opacity: 0.7 },
  tile: { width: 44, height: 44, borderRadius: 14, alignItems: 'center', justifyContent: 'center' },
  actionCopy: { flex: 1 },
  actionTitle: { color: palette.ink, fontSize: 14, lineHeight: 21, fontWeight: '700' },
  subtitle: { color: palette.muted, fontSize: 11, lineHeight: 17, marginTop: 2 },
});
