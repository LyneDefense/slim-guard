import { Ionicons } from '@expo/vector-icons';
import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { brand } from '../../shared/brand/identity';
import { appearance as palette } from '../../shared/ui/appearance';
import { TonalBackdrop } from '../../shared/ui/TonalBackdrop';
import type { CoachProfileData, MobileUser } from '../../types';

export function ProfileHealthCard({ user, profile, onEdit }: { user: MobileUser; profile: CoachProfileData | null; onEdit: () => void }) {
  const name = user.nickname || `${brand.name}用户`;
  const age = profile ? profile.age_band === '80_plus' ? '80 岁及以上' : `${profile.age_band.replace('_', '–')} 岁` : '';
  return <View style={styles.card}>
    <TonalBackdrop tone="indigo" />
    <View style={styles.header}>
      <View style={styles.avatar}><Text style={styles.initial}>{Array.from(user.nickname || '你')[0]}</Text></View>
      <View style={styles.identity}><Text numberOfLines={1} style={styles.name}>{name}</Text><Text numberOfLines={2} style={styles.caption}>健康档案 · {user.identity_hint || '已登录'}</Text></View>
      <Pressable accessibilityRole="button" accessibilityLabel={profile ? '编辑健康档案' : '填写健康档案'} onPress={onEdit} style={styles.edit}>
        <Ionicons name="pencil-outline" size={20} color={palette.surface} />
      </Pressable>
    </View>
    <View style={styles.values}>
      <Value label="当前体重" value={profile?.current_weight_kg} />
      <View style={styles.divider} /><Value label="目标体重" value={profile?.target_weight_kg} />
    </View>
    <View style={styles.footer}>
      <Text style={styles.fact}>{profile ? `${profile.height_cm} cm · ${age}` : '完善资料，让教练更了解你'}</Text>
      {profile && <Text style={styles.fact}>目标日期 {profile.target_date.replaceAll('-', '/')}</Text>}
    </View>
  </View>;
}

function Value({ label, value }: { label: string; value?: number }) {
  return <View style={styles.column} accessible accessibilityLabel={`${label} ${value === undefined ? '未填写' : `${value} kg`}`}>
    <Text style={styles.valueLabel}>{label}</Text><View style={styles.valueRow}>
      <Text numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.6} style={styles.value}>{value === undefined ? '—' : label === '当前体重' ? value.toFixed(1) : String(value)}</Text>
      <Text style={styles.unit}>kg</Text>
    </View>
  </View>;
}

const styles = StyleSheet.create({
  card: { borderRadius: 18, backgroundColor: palette.indigo, overflow: 'hidden', padding: 15 }, header: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  avatar: { width: 38, height: 38, borderRadius: 10, backgroundColor: palette.peach, alignItems: 'center', justifyContent: 'center' },
  initial: { color: palette.orange, fontSize: 22, fontWeight: '600' }, identity: { flex: 1, minWidth: 0 },
  name: { color: palette.surface, fontSize: 16, lineHeight: 23, fontWeight: '700' }, caption: { color: '#DFE4FA', fontSize: 11, lineHeight: 17, marginTop: 2 },
  edit: { width: 44, height: 44, alignItems: 'center', justifyContent: 'center', borderRadius: 22, backgroundColor: 'rgba(255,255,255,0.12)' },
  values: { flexDirection: 'row', alignItems: 'center', gap: 24, marginTop: 19 }, column: { flex: 1, minWidth: 0 },
  divider: { width: StyleSheet.hairlineWidth, height: 54, backgroundColor: 'rgba(255,255,255,0.32)' },
  valueLabel: { color: palette.surface, fontSize: 13, lineHeight: 19 }, valueRow: { flexDirection: 'row', alignItems: 'baseline', gap: 5, marginTop: 3 },
  value: { color: palette.surface, fontSize: 42, lineHeight: 49, fontWeight: '700', letterSpacing: -1.3, flexShrink: 1 }, unit: { color: palette.surface, fontSize: 15, fontWeight: '600' },
  footer: { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: 'rgba(255,255,255,0.3)', marginTop: 14, paddingTop: 11, flexDirection: 'row', flexWrap: 'wrap', columnGap: 14, rowGap: 5, justifyContent: 'space-between' },
  fact: { color: palette.surface, fontSize: 11, lineHeight: 17 },
});
