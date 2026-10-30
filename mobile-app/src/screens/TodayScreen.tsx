import React from 'react';
import { RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { useApp } from '../context/AppContext';
import { RecordHistory } from '../features/record-history/RecordHistory';
import { MetricCard, TodayActions } from '../features/today/TodayCards';
import { DailySummaryCard } from '../features/daily-insights/DailySummaryCard';
import { useDailyInsights } from '../features/daily-insights/useDailyInsights';
import { BrandHeader } from '../shared/brand/BrandHeader';
import { findGoal } from '../lib/memoryFormat';
import { appearance as palette } from '../shared/ui/appearance';

export function TodayScreen({ openCoach }: { openCoach: (draft?: string) => void }) {
  const { data, loading, refresh, online, pending } = useApp();
  const insights = useDailyInsights(data?.user.id, data?.today.routine.timezone || 'Asia/Shanghai', data?.today);
  if (!data) return null;
  const { today, memories, user } = data;
  const targetWeight = data.coach_profile.profile
    ? `${data.coach_profile.profile.target_weight_kg} kg`
    : findGoal(memories, 'goal.target_weight');
  const targetFat = data.coach_profile.profile?.target_body_fat_percent !== null
    && data.coach_profile.profile?.target_body_fat_percent !== undefined
    ? `${data.coach_profile.profile.target_body_fat_percent}%`
    : findGoal(memories, 'goal.target_body_fat');
  const hour = Number(new Intl.DateTimeFormat('en', { hour: 'numeric', hourCycle: 'h23',
    timeZone: today.routine.timezone }).format(new Date()));
  const greeting = hour < 11 ? '早上好' : hour < 18 ? '下午好' : '晚上好';

  return <SafeAreaView style={styles.safe} edges={['top']}>
    <BrandHeader status={online ? '已同步' : pending.length ? `${pending.length} 条待发送` : '未连接'} />
    <ScrollView contentContainerStyle={styles.page}
      refreshControl={<RefreshControl refreshing={loading} onRefresh={refresh} tintColor={palette.indigo} colors={[palette.indigo]} />}>
      <Text style={styles.greeting}>{greeting}，{user.nickname || '今天的你'}</Text>
      <Text style={styles.title} numberOfLines={2} adjustsFontSizeToFit minimumFontScale={0.7}>{!insights.verifying && !insights.error && insights.view?.headline || '今天，吃点什么？'}</Text>
      <View style={styles.metrics}>
        <MetricCard label="最近体重" value={today.current_weight_kg?.toFixed(1) || '—'} unit="kg"
          target={targetWeight ? `目标 ${targetWeight}` : '还没设置目标'} tone="indigo" onPress={() => openCoach('我刚量了体重：')} />
        <MetricCard label="最近体脂" value={today.current_body_fat_percent?.toFixed(1) || '—'} unit="%"
          target={targetFat ? `目标 ${targetFat}` : '还没设置目标'} tone="apricot" onPress={() => openCoach('我刚测了体脂：')} />
      </View>
      <Text accessibilityRole="header" style={styles.sectionTitle}>今天记一点</Text>
      <TodayActions openCoach={openCoach} />
      <DailySummaryCard view={insights.view} busy={insights.busy} verifying={insights.verifying}
        error={insights.error} onRetry={() => { void insights.retry(); }} />
      <RecordHistory timezone={today.routine.timezone} refreshKey={data} onChanged={() => {
        void insights.refresh(); void refresh();
      }} />
    </ScrollView>
  </SafeAreaView>;
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: palette.background },
  page: { paddingHorizontal: 16, paddingTop: 5, paddingBottom: 16 },
  greeting: { color: '#666E86', fontSize: 13, lineHeight: 20, fontWeight: '600', marginBottom: 8 },
  title: { color: palette.ink, fontSize: 28, lineHeight: 36, fontWeight: '800', letterSpacing: -0.6 },
  metrics: { flexDirection: 'row', gap: 12, marginTop: 18 },
  sectionTitle: { color: palette.ink, fontSize: 19, lineHeight: 27, fontWeight: '700', marginTop: 23, marginBottom: 10 },
});
