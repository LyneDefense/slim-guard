import { Ionicons } from '@expo/vector-icons';
import React, { useState } from 'react';
import { Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Card, EmptyState, SectionTitle } from '../components/ui';
import { TrendChart } from '../components/TrendChart';
import { useApp } from '../context/AppContext';
import { colors, radius, spacing } from '../theme';

type Metric = 'weight' | 'bodyFat';

export function ProgressScreen({ openCoach }: { openCoach: (draft?: string) => void }) {
  const { data, refresh, loading } = useApp();
  const [metric, setMetric] = useState<Metric>('weight');
  if (!data) return null;
  const points = metric === 'weight' ? data.progress.weights : data.progress.body_fat;
  const totalLogs = data.progress.meals.length + data.progress.exercise.length + data.progress.weights.length + data.progress.body_fat.length;

  return (
    <SafeAreaView style={styles.safe} edges={['top']}>
      <ScrollView contentContainerStyle={styles.page} refreshControl={<RefreshControl refreshing={loading} onRefresh={refresh} tintColor={colors.primary} />}>
        <Text style={styles.eyebrow}>YOUR PROGRESS</Text>
        <Text style={styles.title}>看见变化，{`\n`}也看见坚持。</Text>
        <View style={styles.segment}>
          <Segment title="体重" selected={metric === 'weight'} onPress={() => setMetric('weight')} />
          <Segment title="体脂" selected={metric === 'bodyFat'} onPress={() => setMetric('bodyFat')} />
        </View>
        <Card style={styles.chartCard}>
          <Text style={styles.cardLabel}>{metric === 'weight' ? '体重趋势' : '体脂趋势'}</Text>
          <TrendChart points={points} unit={metric === 'weight' ? 'kg' : '%'} color={metric === 'weight' ? colors.primary : colors.accent} />
          <Pressable style={styles.addRecord} onPress={() => openCoach(metric === 'weight' ? '我刚量了体重：' : '我刚测了体脂：')}>
            <Ionicons name="add" size={18} color={colors.primary} />
            <Text style={styles.addRecordText}>补一条记录</Text>
          </Pressable>
        </Card>

        <SectionTitle title="最近 60 条里的积累" />
        <View style={styles.statGrid}>
          <Stat icon="scale-outline" label="体重" value={data.progress.weights.length} />
          <Stat icon="fitness-outline" label="体脂" value={data.progress.body_fat.length} />
          <Stat icon="restaurant-outline" label="饮食" value={data.progress.meals.length} />
          <Stat icon="walk-outline" label="运动" value={data.progress.exercise.length} />
        </View>

        <SectionTitle title="最近活动" />
        {totalLogs === 0 ? (
          <Card><EmptyState icon="analytics-outline" title="还没有可画出的轨迹" body="先记一次体重、饮食或运动，这里就会开始长出属于你的趋势。" /></Card>
        ) : (
          <Card>
            {recentActivity(data.progress).map((item, index) => (
              <View key={item.id} style={[styles.activity, index > 0 && styles.activityBorder]}>
                <View style={styles.activityIcon}><Ionicons name={item.icon} size={18} color={colors.primary} /></View>
                <View style={{ flex: 1 }}><Text style={styles.activityTitle}>{item.title}</Text><Text style={styles.activityDate}>{item.date}</Text></View>
                <Text style={styles.activityValue}>{item.value}</Text>
              </View>
            ))}
          </Card>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

function Segment({ title, selected, onPress }: { title: string; selected: boolean; onPress: () => void }) {
  return <Pressable onPress={onPress} style={[styles.segmentItem, selected && styles.segmentSelected]}><Text style={[styles.segmentText, selected && styles.segmentTextSelected]}>{title}</Text></Pressable>;
}

function Stat({ icon, label, value }: { icon: keyof typeof Ionicons.glyphMap; label: string; value: number }) {
  return <View style={styles.stat}><Ionicons name={icon} size={19} color={colors.primary} /><Text style={styles.statValue}>{value}</Text><Text style={styles.statLabel}>{label}记录</Text></View>;
}

function recentActivity(progress: NonNullable<ReturnType<typeof useApp>['data']>['progress']) {
  const date = (value: unknown) => new Date(String(value));
  return [
    ...progress.weights.map((item) => ({ id: `w-${item.id}`, at: date(item.occurred_at), icon: 'scale-outline' as const, title: '记录体重', value: `${item.value} kg` })),
    ...progress.body_fat.map((item) => ({ id: `f-${item.id}`, at: date(item.occurred_at), icon: 'fitness-outline' as const, title: '记录体脂', value: `${item.value}%` })),
    ...progress.meals.map((item) => ({ id: `m-${item.id}`, at: date(item.occurred_at), icon: 'restaurant-outline' as const, title: '记录饮食', value: '已记录' })),
    ...progress.exercise.map((item) => ({ id: `e-${item.id}`, at: date(item.occurred_at), icon: 'walk-outline' as const, title: String(item.activity_name || '记录运动'), value: item.duration_minutes ? `${item.duration_minutes} 分钟` : '已记录' })),
  ].sort((a, b) => b.at.getTime() - a.at.getTime()).slice(0, 8).map((item) => ({ ...item, date: `${item.at.getMonth() + 1}月${item.at.getDate()}日 ${item.at.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}` }));
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  page: { padding: spacing.lg, paddingTop: spacing.xl, paddingBottom: 110 },
  eyebrow: { color: colors.primary, fontSize: 11, fontWeight: '800', letterSpacing: 1.7 },
  title: { color: colors.ink, fontSize: 31, lineHeight: 39, fontWeight: '800', letterSpacing: -1, marginTop: spacing.sm },
  segment: { flexDirection: 'row', backgroundColor: colors.surfaceMuted, borderRadius: radius.md, padding: 4, marginTop: spacing.xl, marginBottom: spacing.md },
  segmentItem: { flex: 1, minHeight: 40, borderRadius: 12, alignItems: 'center', justifyContent: 'center' },
  segmentSelected: { backgroundColor: colors.surface },
  segmentText: { color: colors.inkMuted, fontSize: 14, fontWeight: '600' },
  segmentTextSelected: { color: colors.ink, fontWeight: '800' },
  chartCard: { overflow: 'hidden' },
  cardLabel: { color: colors.inkMuted, fontSize: 13, fontWeight: '600', marginBottom: spacing.sm },
  addRecord: { alignSelf: 'center', flexDirection: 'row', alignItems: 'center', gap: 5, backgroundColor: colors.primarySoft, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, borderRadius: radius.pill, marginTop: spacing.md },
  addRecordText: { color: colors.primary, fontSize: 13, fontWeight: '700' },
  statGrid: { flexDirection: 'row', gap: spacing.sm },
  stat: { flex: 1, backgroundColor: colors.surface, borderRadius: radius.md, paddingVertical: spacing.md, alignItems: 'center', borderWidth: StyleSheet.hairlineWidth, borderColor: colors.line },
  statValue: { color: colors.ink, fontSize: 22, fontWeight: '800', marginTop: spacing.xs },
  statLabel: { color: colors.inkMuted, fontSize: 10, marginTop: 2 },
  activity: { minHeight: 60, flexDirection: 'row', alignItems: 'center', gap: spacing.md },
  activityBorder: { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.line },
  activityIcon: { width: 36, height: 36, borderRadius: 13, backgroundColor: colors.primarySoft, alignItems: 'center', justifyContent: 'center' },
  activityTitle: { color: colors.ink, fontSize: 14, fontWeight: '700' },
  activityDate: { color: colors.inkMuted, fontSize: 11, marginTop: 3 },
  activityValue: { color: colors.ink, fontSize: 13, fontWeight: '600' },
});
