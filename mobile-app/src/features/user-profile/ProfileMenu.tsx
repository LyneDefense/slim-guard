import { Ionicons } from '@expo/vector-icons';
import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { appearance as palette } from '../../shared/ui/appearance';
import type { ProfilePage } from './ProfileDetails';

export function ProfileMenu({ reminderCount, connectionLabel, onOpen }: { reminderCount: number; connectionLabel: string; onOpen: (page: ProfilePage) => void }) {
  return <>
    <Text accessibilityRole="header" style={styles.heading}>为你定制</Text>
    <View style={styles.card}>
      <Row title="教练记住的你" subtitle="目标、习惯与限制" icon="chatbubble-ellipses-outline" tone="lavender" onPress={() => onOpen('memory')} />
      <Row title="日常提醒" subtitle="称重、饮食与晚间复盘" value={`${reminderCount} 项开启`} icon="notifications-outline" tone="gold" onPress={() => onOpen('reminders')} divider />
    </View>
    <Text accessibilityRole="header" style={styles.heading}>连接与账号</Text>
    <View style={styles.card}>
      <Row title="连接微信" subtitle="App 与微信共用记录和记忆" value={connectionLabel} icon="chatbubbles-outline" tone="blue" onPress={() => onOpen('wecom')} />
      <Row title="账号与说明" subtitle="资料安全、健康边界与账号管理" icon="shield-checkmark-outline" tone="peach" onPress={() => onOpen('account')} divider />
    </View>
  </>;
}

function Row({ title, subtitle, value, icon, tone, onPress, divider }: {
  title: string; subtitle: string; value?: string; icon: keyof typeof Ionicons.glyphMap; tone: 'lavender' | 'gold' | 'blue' | 'peach'; onPress: () => void; divider?: boolean;
}) {
  return <Pressable accessibilityRole="button" accessibilityLabel={`${title}${value ? `，${value}` : ''}`} onPress={onPress} style={({ pressed }) => [styles.row, divider && styles.divider, pressed && styles.pressed]}>
    <View style={[styles.icon, { backgroundColor: palette[tone] }]}><Ionicons name={icon} size={26} color={tone === 'gold' || tone === 'peach' ? palette.orange : palette.ink} /></View>
    <View style={styles.copy}><Text style={styles.title}>{title}</Text><Text style={styles.subtitle}>{subtitle}</Text></View>
    {value && <Text style={styles.hint}>{value}</Text>}<Ionicons name="chevron-forward" size={17} color={palette.muted} />
  </Pressable>;
}

const styles = StyleSheet.create({
  heading: { color: palette.ink, fontSize: 19, lineHeight: 27, fontWeight: '700', marginTop: 20, marginBottom: 10 },
  card: { ...palette.shadow, borderRadius: 18, paddingHorizontal: 12, backgroundColor: palette.surface },
  row: { minHeight: 66, flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 10 }, divider: { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: palette.line },
  icon: { width: 44, height: 44, borderRadius: 14, alignItems: 'center', justifyContent: 'center' }, copy: { flex: 1 },
  title: { color: palette.ink, fontSize: 14, lineHeight: 21, fontWeight: '700' }, subtitle: { color: palette.muted, fontSize: 11, lineHeight: 17, marginTop: 3 },
  hint: { color: palette.muted, fontSize: 11, lineHeight: 17, maxWidth: 70 }, pressed: { opacity: 0.7 },
});
