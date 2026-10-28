import { Ionicons } from '@expo/vector-icons';
import React, { useState } from 'react';
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Card, SectionTitle } from '../components/ui';
import { TrendChart } from '../features/progress/TrendChart';
import { usePeriodProgress } from '../features/progress/usePeriodProgress';
import { useApp } from '../context/AppContext';
import { colors, radius, spacing } from '../theme';

type Metric = 'weight' | 'bodyFat';

export function ProgressScreen({ openCoach }: { openCoach: (draft?: string) => void }) {
  const { data, refresh, loading } = useApp();
  const [metric, setMetric] = useState<Metric>('weight');
  const period = usePeriodProgress(data?.user.id, data?.routine.timezone, data);
  if (!data) return null;
  const summary = period.data;

  return (
    <SafeAreaView style={styles.safe} edges={['top']}>
      <ScrollView contentContainerStyle={styles.page} refreshControl={<RefreshControl refreshing={loading || period.loading} onRefresh={refresh} tintColor={colors.primary} />}>
        <Text style={styles.eyebrow}>YOUR PROGRESS</Text>
        <Text style={styles.title}>看见变化，{`\n`}也看见坚持。</Text>
        <View style={styles.periodSelector}>
          <Segment title="近 7 天" selected={period.days === 7} onPress={() => period.setDays(7)} />
          <Segment title="近 30 天" selected={period.days === 30} onPress={() => period.setDays(30)} />
        </View>
        {summary ? (
          <Text style={styles.periodDate}>
            {summary.from_date.replaceAll('-', '/')} — {summary.to_date.replaceAll('-', '/')} · 含今天
          </Text>
        ) : null}
        <View style={styles.segment}>
          <Segment title="体重" selected={metric === 'weight'} onPress={() => setMetric('weight')} />
          <Segment title="体脂" selected={metric === 'bodyFat'} onPress={() => setMetric('bodyFat')} />
        </View>
        {period.loading ? (
          <Card><ActivityIndicator accessibilityLabel="正在加载统计" color={colors.primary} /></Card>
        ) : null}
        {period.error ? (
          <Card>
            <Text accessibilityRole="alert" style={styles.error}>暂时无法加载统计，请重试。</Text>
            <Pressable accessibilityRole="button" onPress={period.reload} style={styles.retry}>
              <Text style={styles.addRecordText}>重新加载</Text>
            </Pressable>
          </Card>
        ) : null}
        {summary ? (
          <>
            <Card style={styles.chartCard}>
              <Text style={styles.cardLabel}>{metric === 'weight' ? '体重趋势' : '体脂趋势'}</Text>
              <TrendChart
                points={metric === 'weight' ? summary.weights : summary.body_fat}
                fromDate={summary.from_date} toDate={summary.to_date}
                unit={metric === 'weight' ? 'kg' : '%'}
                color={metric === 'weight' ? colors.primary : colors.accent}
              />
              <Pressable style={styles.addRecord} onPress={() => openCoach(metric === 'weight' ? '我刚量了体重：' : '我刚测了体脂：')}>
                <Ionicons name="add" size={18} color={colors.primary} />
                <Text style={styles.addRecordText}>补一条记录</Text>
              </Pressable>
            </Card>
            <SectionTitle title={`近 ${period.days} 天的记录`} />
            <View style={styles.statGrid}>
              <Stat icon="scale-outline" label="体重" value={summary.counts.weight} />
              <Stat icon="fitness-outline" label="体脂" value={summary.counts.body_fat} />
              <Stat icon="restaurant-outline" label="饮食" value={summary.counts.meal} />
              <Stat icon="walk-outline" label="运动" value={summary.counts.exercise} />
            </View>
            <Text style={styles.statsHint}>按实际记录日期统计有效记录条数；曲线每天显示一个最新测量点，未记录的日期不补零。</Text>
          </>
        ) : null}
      </ScrollView>
    </SafeAreaView>
  );
}

function Segment({ title, selected, onPress }: { title: string; selected: boolean; onPress: () => void }) {
  return <Pressable accessibilityRole="radio" accessibilityState={{ checked: selected }} onPress={onPress} style={[styles.segmentItem, selected && styles.segmentSelected]}><Text style={[styles.segmentText, selected && styles.segmentTextSelected]}>{title}</Text></Pressable>;
}

function Stat({ icon, label, value }: { icon: keyof typeof Ionicons.glyphMap; label: string; value: number }) {
  return <View style={styles.stat}><Ionicons name={icon} size={19} color={colors.primary} /><Text style={styles.statValue}>{value}</Text><Text style={styles.statLabel}>{label}记录</Text></View>;
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  page: { padding: spacing.lg, paddingTop: spacing.xl, paddingBottom: 110 },
  eyebrow: { color: colors.primary, fontSize: 11, fontWeight: '800', letterSpacing: 1.7 },
  title: { color: colors.ink, fontSize: 31, lineHeight: 39, fontWeight: '800', letterSpacing: -1, marginTop: spacing.sm },
  periodSelector: { flexDirection: 'row', backgroundColor: colors.surfaceMuted, padding: 4, borderRadius: radius.md, marginTop: spacing.xl },
  periodDate: { color: colors.inkMuted, fontSize: 12, marginTop: spacing.sm },
  segment: { flexDirection: 'row', backgroundColor: colors.surfaceMuted, borderRadius: radius.md, padding: 4, marginTop: spacing.xl, marginBottom: spacing.md },
  segmentItem: { flex: 1, minHeight: 44, borderRadius: 12, alignItems: 'center', justifyContent: 'center' },
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
  statsHint: { color: colors.inkMuted, fontSize: 12, lineHeight: 19, marginTop: spacing.md },
  error: { color: colors.danger, fontSize: 14 }, retry: { minHeight: 44, justifyContent: 'center', alignItems: 'center', marginTop: spacing.sm },
});
