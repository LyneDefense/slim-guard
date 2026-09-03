import { Ionicons } from '@expo/vector-icons';
import React from 'react';
import { Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { BrandMark, Card, SectionTitle } from '../components/ui';
import { useApp } from '../context/AppContext';
import { findGoal } from '../lib/memoryFormat';
import { colors, radius, spacing } from '../theme';

export function TodayScreen({ openCoach }: { openCoach: (draft?: string) => void }) {
  const { data, loading, refresh, online, pending } = useApp();
  if (!data) return null;
  const { today, memories, user } = data;
  const targetWeight = findGoal(memories, 'goal.target_weight');
  const targetFat = findGoal(memories, 'goal.target_body_fat');
  const hour = new Date().getHours();
  const greeting = hour < 11 ? '早上好' : hour < 18 ? '下午好' : '晚上好';
  const displayName = user.nickname || '今天的你';

  return (
    <SafeAreaView style={styles.safe} edges={['top']}>
      <ScrollView
        contentContainerStyle={styles.page}
        refreshControl={<RefreshControl refreshing={loading} onRefresh={refresh} tintColor={colors.primary} />}
      >
        <View style={styles.header}>
          <BrandMark compact />
          <View style={[styles.statusDot, { backgroundColor: online ? colors.primarySoft : colors.accentSoft }]}>
            <View style={[styles.dot, { backgroundColor: online ? colors.primary : colors.warning }]} />
            <Text style={styles.statusText}>{online ? '已同步' : `${pending.length} 条待发送`}</Text>
          </View>
        </View>

        <Text style={styles.eyebrow}>{greeting}，{displayName}</Text>
        <Text style={styles.title}>不用完美，{`\n`}今天往前一点就好。</Text>

        <View style={styles.metrics}>
          <MetricCard
            label="最近体重"
            value={today.current_weight_kg?.toFixed(1) || '—'}
            unit="kg"
            target={targetWeight ? `目标 ${targetWeight}` : '还没设置目标'}
            tone="green"
            onPress={() => openCoach('我刚量了体重：')}
          />
          <MetricCard
            label="最近体脂"
            value={today.current_body_fat_percent?.toFixed(1) || '—'}
            unit="%"
            target={targetFat ? `目标 ${targetFat}` : '还没设置目标'}
            tone="orange"
            onPress={() => openCoach('我刚测了体脂：')}
          />
        </View>

        <SectionTitle title="今天记一点" />
        <View style={styles.quickGrid}>
          <QuickAction icon="scale-outline" title="记体重" subtitle="看长期趋势" onPress={() => openCoach('我刚量了体重：')} />
          <QuickAction icon="restaurant-outline" title="记饮食" subtitle="文字或拍照" onPress={() => openCoach('我刚吃了')} />
          <QuickAction icon="walk-outline" title="记运动" subtitle="做了什么都算" onPress={() => openCoach('我刚运动了')} />
          <QuickAction icon="chatbubble-ellipses-outline" title="聊一聊" subtitle="问教练任何事" onPress={() => openCoach()} />
        </View>

        <SectionTitle title="今日节奏" />
        <Card>
          <PulseRow icon="restaurant" title="饮食记录" value={`${today.meals_logged} 次`} done={today.meals_logged > 0} />
          <View style={styles.divider} />
          <PulseRow icon="walk" title="运动记录" value={`${today.exercise_logged} 次`} done={today.exercise_logged > 0} />
          <View style={styles.coachNote}>
            <Ionicons name="sparkles" size={17} color={colors.primary} />
            <Text style={styles.coachText}>{today.meals_logged + today.exercise_logged > 0 ? '已经留下记录了。下一步让身体舒服一点就好。' : '还没记录也没关系，从最容易的一件开始。'}</Text>
          </View>
        </Card>

        {memories.length === 0 ? (
          <Pressable style={styles.onboarding} onPress={() => openCoach('我想先告诉你我的身体情况和减脂目标')}>
            <View style={styles.onboardingIcon}><Ionicons name="compass-outline" size={25} color={colors.primaryDark} /></View>
            <View style={{ flex: 1 }}>
              <Text style={styles.onboardingTitle}>先让教练认识你</Text>
              <Text style={styles.onboardingText}>告诉我身高、当前体重、目标和生活习惯，我会自己理解并记住。</Text>
            </View>
            <Ionicons name="chevron-forward" size={20} color={colors.primary} />
          </Pressable>
        ) : null}
      </ScrollView>
    </SafeAreaView>
  );
}

function MetricCard({ label, value, unit, target, tone, onPress }: { label: string; value: string; unit: string; target: string; tone: 'green' | 'orange'; onPress: () => void }) {
  return (
    <Pressable onPress={onPress} style={[styles.metricCard, tone === 'orange' && styles.metricOrange]}>
      <Text style={styles.metricLabel}>{label}</Text>
      <View style={styles.metricValueRow}><Text style={styles.metricValue}>{value}</Text><Text style={styles.metricUnit}>{unit}</Text></View>
      <Text numberOfLines={1} style={styles.metricTarget}>{target}</Text>
    </Pressable>
  );
}

function QuickAction({ icon, title, subtitle, onPress }: { icon: keyof typeof Ionicons.glyphMap; title: string; subtitle: string; onPress: () => void }) {
  return (
    <Pressable onPress={onPress} style={({ pressed }) => [styles.quick, pressed && { opacity: 0.7 }]}>
      <View style={styles.quickIcon}><Ionicons name={icon} size={22} color={colors.primary} /></View>
      <View><Text style={styles.quickTitle}>{title}</Text><Text style={styles.quickSubtitle}>{subtitle}</Text></View>
    </Pressable>
  );
}

function PulseRow({ icon, title, value, done }: { icon: keyof typeof Ionicons.glyphMap; title: string; value: string; done: boolean }) {
  return (
    <View style={styles.pulseRow}>
      <View style={[styles.pulseIcon, done && styles.pulseIconDone]}><Ionicons name={done ? 'checkmark' : icon} size={18} color={done ? colors.white : colors.primary} /></View>
      <Text style={styles.pulseTitle}>{title}</Text>
      <Text style={styles.pulseValue}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  page: { padding: spacing.lg, paddingBottom: 110 },
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: spacing.xxl },
  statusDot: { borderRadius: radius.pill, paddingHorizontal: 10, paddingVertical: 7, flexDirection: 'row', alignItems: 'center', gap: 6 },
  dot: { width: 7, height: 7, borderRadius: 4 },
  statusText: { color: colors.inkMuted, fontSize: 12, fontWeight: '600' },
  eyebrow: { color: colors.primary, fontSize: 14, fontWeight: '700', marginBottom: spacing.sm },
  title: { color: colors.ink, fontSize: 32, lineHeight: 40, fontWeight: '800', letterSpacing: -1 },
  metrics: { flexDirection: 'row', gap: spacing.md, marginTop: spacing.xl },
  metricCard: { flex: 1, minHeight: 150, padding: spacing.lg, borderRadius: radius.lg, backgroundColor: colors.primarySoft },
  metricOrange: { backgroundColor: colors.accentSoft },
  metricLabel: { color: colors.inkMuted, fontSize: 13, fontWeight: '600' },
  metricValueRow: { flexDirection: 'row', alignItems: 'baseline', marginTop: spacing.md },
  metricValue: { color: colors.ink, fontSize: 34, fontWeight: '800', letterSpacing: -1 },
  metricUnit: { color: colors.inkMuted, fontSize: 13, fontWeight: '600', marginLeft: 4 },
  metricTarget: { color: colors.inkMuted, fontSize: 12, marginTop: 'auto' },
  quickGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.md },
  quick: { width: '48%', flexGrow: 1, backgroundColor: colors.surface, borderRadius: radius.md, padding: spacing.md, flexDirection: 'row', alignItems: 'center', gap: 10, borderWidth: StyleSheet.hairlineWidth, borderColor: colors.line },
  quickIcon: { width: 40, height: 40, borderRadius: 14, backgroundColor: colors.primarySoft, alignItems: 'center', justifyContent: 'center' },
  quickTitle: { color: colors.ink, fontSize: 14, fontWeight: '700' },
  quickSubtitle: { color: colors.inkMuted, fontSize: 11, marginTop: 2 },
  pulseRow: { flexDirection: 'row', alignItems: 'center', minHeight: 44 },
  pulseIcon: { width: 32, height: 32, borderRadius: 12, backgroundColor: colors.primarySoft, alignItems: 'center', justifyContent: 'center' },
  pulseIconDone: { backgroundColor: colors.primary },
  pulseTitle: { color: colors.ink, fontSize: 15, fontWeight: '600', marginLeft: spacing.md },
  pulseValue: { color: colors.inkMuted, fontSize: 14, marginLeft: 'auto' },
  divider: { height: StyleSheet.hairlineWidth, backgroundColor: colors.line, marginVertical: spacing.sm },
  coachNote: { backgroundColor: colors.surfaceMuted, borderRadius: radius.md, padding: spacing.md, flexDirection: 'row', gap: spacing.sm, marginTop: spacing.md },
  coachText: { color: colors.inkMuted, fontSize: 13, lineHeight: 19, flex: 1 },
  onboarding: { marginTop: spacing.xl, padding: spacing.lg, backgroundColor: colors.accentSoft, borderRadius: radius.lg, flexDirection: 'row', alignItems: 'center', gap: spacing.md },
  onboardingIcon: { width: 46, height: 46, borderRadius: 17, backgroundColor: colors.white, alignItems: 'center', justifyContent: 'center' },
  onboardingTitle: { color: colors.ink, fontSize: 16, fontWeight: '800' },
  onboardingText: { color: colors.inkMuted, fontSize: 12, lineHeight: 18, marginTop: 3 },
});
