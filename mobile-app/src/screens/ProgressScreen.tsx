import React, { useState } from 'react';
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { useApp } from '../context/AppContext';
import { MeasurementJournal } from '../features/progress/MeasurementJournal';
import { PeriodCounts } from '../features/progress/PeriodCounts';
import { PeriodSummary } from '../features/progress/PeriodSummary';
import { measurementDates, shortDate } from '../features/progress/presentation';
import type { MeasurementMetric } from '../features/progress/types';
import { usePeriodProgress } from '../features/progress/usePeriodProgress';
import { BrandHeader } from '../shared/brand/BrandHeader';
import { appearance as palette } from '../shared/ui/appearance';

export function ProgressScreen({ openCoach }: { openCoach: (draft?: string) => void }) {
  const { data, refresh, loading, online, pending } = useApp();
  const [metric, setMetric] = useState<MeasurementMetric>('weight');
  const period = usePeriodProgress(data?.user.id, data?.routine.timezone, data);
  if (!data) return null;
  const summary = period.data;
  const points = summary ? measurementDates(metric === 'weight' ? summary.weights : summary.body_fat, summary.from_date, summary.to_date) : [];

  return <SafeAreaView style={styles.safe} edges={['top']}>
    <BrandHeader status={online ? '已同步' : pending.length ? `${pending.length} 条待发送` : '未连接'} />
    <ScrollView contentContainerStyle={styles.page}
      refreshControl={<RefreshControl refreshing={loading || period.loading} onRefresh={() => { void refresh(); period.reload(); }} tintColor={palette.indigo} colors={[palette.indigo]} />}>
      <Text accessibilityRole="header" style={styles.title}>记录与变化</Text>
      <View style={styles.periodRow}>
        <View style={styles.periodSelector} accessibilityRole="tablist">
          {([7, 30] as const).map(days => <Pressable key={days} hitSlop={4} accessibilityRole="tab" accessibilityLabel={`近 ${days} 天`}
            accessibilityState={{ selected: period.days === days }} onPress={() => period.setDays(days)} style={[styles.period, period.days === days && styles.periodSelected]}>
            <Text style={[styles.periodText, period.days === days && styles.periodTextSelected]}>近 {days} 天</Text>
          </Pressable>)}
        </View>
        {summary && <Text style={styles.date}>{shortDate(summary.from_date)} — {shortDate(summary.to_date)}</Text>}
      </View>
      {period.loading && <View style={styles.stateCard}><ActivityIndicator accessibilityLabel="正在加载统计" color={palette.indigo} /></View>}
      {period.error && <View style={styles.stateCard}>
        <Text accessibilityRole="alert" style={styles.error}>暂时无法加载统计，请重试。</Text>
        <Pressable accessibilityRole="button" onPress={period.reload} style={styles.retry}><Text style={styles.retryText}>重新加载</Text></Pressable>
      </View>}
      {summary && <>
        <PeriodSummary points={points} metric={metric} />
        <MeasurementJournal key={`${data.user.id}|${summary.from_date}|${summary.to_date}|${metric}`} metric={metric} onMetricChange={setMetric}
          points={points} fromDate={summary.from_date} toDate={summary.to_date} openCoach={openCoach} />
        <PeriodCounts counts={summary.counts} />
      </>}
    </ScrollView>
  </SafeAreaView>;
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: palette.background }, page: { paddingHorizontal: 16, paddingTop: 0, paddingBottom: 16 },
  title: { color: palette.ink, fontSize: 28, lineHeight: 33, fontWeight: '800', letterSpacing: -0.5, marginBottom: 6 },
  periodRow: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', columnGap: 8, rowGap: 4, marginBottom: 8 },
  periodSelector: { flexDirection: 'row', backgroundColor: '#F4F3F5', borderRadius: 12 },
  period: { minWidth: 82, minHeight: 36, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 13, borderRadius: 12 },
  periodSelected: { backgroundColor: '#E9EAFC' }, periodText: { color: palette.muted, fontSize: 14, fontWeight: '500' },
  periodTextSelected: { color: palette.ink, fontWeight: '700' }, date: { color: palette.muted, fontSize: 12, lineHeight: 20 },
  stateCard: { backgroundColor: palette.surface, borderRadius: 18, padding: 24, marginTop: 8, gap: 10 },
  error: { color: '#BA4A45', fontSize: 14, lineHeight: 22 }, retry: { minHeight: 44, justifyContent: 'center', alignItems: 'center' },
  retryText: { color: palette.indigo, fontSize: 14, fontWeight: '700' },
});
