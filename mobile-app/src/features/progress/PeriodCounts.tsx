import React from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { AppIcon } from '../../shared/ui/AppIcon';
import { appearance as palette } from '../../shared/ui/appearance';
import type { PeriodProgress } from './types';

export function PeriodCounts({ counts }: { counts: PeriodProgress['counts'] }) {
  return <View>
    <Text accessibilityRole="header" style={styles.title}>同期记录</Text>
    <View style={styles.card}>
      {([
        { key: 'weight', label: '体重', icon: 'scale', background: palette.peach, color: palette.orange },
        { key: 'body_fat', label: '体脂', icon: null, background: palette.blue, color: palette.indigo },
        { key: 'meal', label: '饮食', icon: 'meal', background: palette.lavender, color: palette.indigo },
        { key: 'exercise', label: '运动', icon: 'exercise', background: palette.gold, color: palette.orange },
      ] as const).map((item, index) => <View key={item.key} accessible accessibilityLabel={`${item.label}记录 ${counts[item.key]} 条`}
        style={[styles.stat, index > 0 && styles.divider]}>
        <View style={[styles.icon, { backgroundColor: item.background }]}>
          {item.icon ? <AppIcon name={item.icon} size={22} color={item.color} /> : <Text style={styles.percent}>%</Text>}
        </View>
        <View style={styles.copy}>
          <Text style={styles.label}>{item.label}</Text>
          <Text numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.6} style={styles.value}>{counts[item.key].toLocaleString('zh-CN')}<Text style={styles.unit}> 条</Text></Text>
        </View>
      </View>)}
    </View>
  </View>;
}

const styles = StyleSheet.create({
  title: { fontSize: 19, lineHeight: 25, color: palette.ink, fontWeight: '700', marginTop: 12, marginBottom: 6 },
  card: { ...palette.shadow, backgroundColor: palette.surface, borderRadius: 16, flexDirection: 'row', paddingHorizontal: 8, paddingVertical: 12 },
  stat: { flex: 1, minWidth: 0, flexDirection: 'row', alignItems: 'center', paddingHorizontal: 5, gap: 7 },
  divider: { borderLeftWidth: StyleSheet.hairlineWidth, borderLeftColor: palette.line },
  icon: { width: 31, height: 36, borderRadius: 12, alignItems: 'center', justifyContent: 'center' },
  copy: { flex: 1, minWidth: 0 }, label: { fontSize: 10, lineHeight: 15, color: palette.muted },
  percent: { color: palette.indigo, fontSize: 24, fontWeight: '500' },
  value: { color: palette.ink, fontSize: 17, fontWeight: '700', lineHeight: 23 }, unit: { fontSize: 10, fontWeight: '500' },
});
