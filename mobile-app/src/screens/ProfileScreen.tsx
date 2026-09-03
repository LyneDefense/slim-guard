import { Ionicons } from '@expo/vector-icons';
import React, { useEffect, useState } from 'react';
import { Alert, Pressable, ScrollView, StyleSheet, Switch, Text, TextInput, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Button, Card, EmptyState, SectionTitle } from '../components/ui';
import { useApp } from '../context/AppContext';
import { memoryLabel, memoryText } from '../lib/memoryFormat';
import { colors, radius, spacing } from '../theme';
import type { Routine } from '../types';

export function ProfileScreen({ openCoach }: { openCoach: (draft?: string) => void }) {
  const { data, updateProfile, updateRoutine, logout, loading } = useApp();
  const [nickname, setNickname] = useState('');
  const [routine, setRoutine] = useState<Routine | null>(null);
  const [savingProfile, setSavingProfile] = useState(false);
  const [savingRoutine, setSavingRoutine] = useState(false);

  useEffect(() => {
    if (!data) return;
    setNickname(data.user.nickname || '');
    setRoutine(data.routine);
  }, [data]);
  if (!data || !routine) return null;

  async function saveName() {
    setSavingProfile(true);
    try {
      await updateProfile(nickname);
      Alert.alert('保存好了', nickname.trim() ? `以后叫你“${nickname.trim()}”。` : '已经清除昵称。');
    } catch (caught) {
      Alert.alert('没有保存成功', caught instanceof Error ? caught.message : '请稍后重试');
    } finally {
      setSavingProfile(false);
    }
  }

  async function saveReminders() {
    if (!routine) return;
    const invalid = [routine.weight_reminder_time, routine.meal_reminder_time, routine.daily_review_time]
      .some((value) => value !== null && !/^([01]\d|2[0-3]):[0-5]\d$/.test(value));
    if (invalid) {
      Alert.alert('时间格式不对', '请用 24 小时制，例如 08:30 或 21:00。');
      return;
    }
    setSavingRoutine(true);
    try {
      const notificationsAllowed = await updateRoutine(routine);
      Alert.alert('提醒已保存', notificationsAllowed ? '会按设备本地时间提醒你。' : '系统通知权限没有开启，设置已保存但暂时不会弹提醒。');
    } catch (caught) {
      Alert.alert('没有保存成功', caught instanceof Error ? caught.message : '请稍后重试');
    } finally {
      setSavingRoutine(false);
    }
  }

  return (
    <SafeAreaView style={styles.safe} edges={['top']}>
      <ScrollView contentContainerStyle={styles.page} keyboardShouldPersistTaps="handled">
        <Text style={styles.eyebrow}>ABOUT YOU</Text>
        <Text style={styles.title}>你的资料，{`\n`}由你说了算。</Text>

        <Card style={styles.profileCard}>
          <View style={styles.avatar}><Text style={styles.avatarText}>{(data.user.nickname || 'S').slice(0, 1).toUpperCase()}</Text></View>
          <View style={styles.profileMeta}>
            <Text style={styles.profileName}>{data.user.nickname || 'SlimGuard 用户'}</Text>
            <Text style={styles.profileHint}>{data.user.identity_hint || '手机号已验证'}</Text>
          </View>
        </Card>

        <SectionTitle title="称呼" />
        <Card>
          <Text style={styles.help}>教练在对话里会这样称呼你</Text>
          <TextInput value={nickname} onChangeText={setNickname} maxLength={80} placeholder="你希望怎么称呼" placeholderTextColor="#919893" style={styles.input} />
          <Button title="保存称呼" variant="secondary" loading={savingProfile} onPress={() => void saveName()} />
        </Card>

        <SectionTitle title="教练记住的你" action={<Pressable onPress={() => openCoach('我想更新一项个人资料：')}><Text style={styles.textAction}>告诉教练有变化</Text></Pressable>} />
        <Card>
          {data.memories.length === 0 ? (
            <EmptyState icon="library-outline" title="还没有长期记忆" body="在对话里自然告诉教练你的目标、习惯和限制，它会在证据足够时自动保存。" />
          ) : data.memories.map((memory, index) => (
            <View key={memory.id} style={[styles.memoryRow, index > 0 && styles.rowBorder]}>
              <View style={[styles.memoryIcon, memory.kind === 'goal' && styles.goalIcon, memory.kind === 'constraint' && styles.constraintIcon]}>
                <Ionicons name={memory.kind === 'goal' ? 'flag' : memory.kind === 'constraint' ? 'shield-checkmark' : 'person'} size={16} color={memory.kind === 'constraint' ? colors.warning : colors.primary} />
              </View>
              <View style={{ flex: 1 }}>
                <View style={styles.memoryLabelRow}>
                  <Text style={styles.memoryLabel}>{memoryLabel(memory)}</Text>
                  {memory.stale ? <Text style={styles.reviewBadge}>待确认</Text> : null}
                </View>
                <Text style={styles.memoryText}>{memoryText(memory)}</Text>
              </View>
            </View>
          ))}
        </Card>

        <SectionTitle title="日常提醒" />
        <Card>
          <ReminderRow icon="scale-outline" title="称重提醒" value={routine.weight_reminder_time} fallback="08:00" onChange={(value) => setRoutine({ ...routine, weight_reminder_time: value })} />
          <View style={styles.rowBorder} />
          <ReminderRow icon="restaurant-outline" title="饮食记录" value={routine.meal_reminder_time} fallback="12:30" onChange={(value) => setRoutine({ ...routine, meal_reminder_time: value })} />
          <View style={styles.rowBorder} />
          <ReminderRow icon="moon-outline" title="晚间复盘" value={routine.daily_review_time} fallback="21:30" onChange={(value) => setRoutine({ ...routine, daily_review_time: value })} />
          <Button title="保存提醒" variant="secondary" loading={savingRoutine} onPress={() => void saveReminders()} style={{ marginTop: spacing.lg }} />
        </Card>

        <SectionTitle title="账号与说明" />
        <Card>
          <InfoRow icon="shield-checkmark-outline" title="资料安全" body="登录凭证保存在系统安全区，健康资料以服务端记录为准。" />
          <View style={styles.rowBorder} />
          <InfoRow icon="medkit-outline" title="健康边界" body="SlimGuard 用于日常减脂管理，不替代医生、营养师的诊断或治疗。" />
          <Button title="退出登录" variant="ghost" loading={loading} onPress={() => Alert.alert('退出登录？', '离线待发送的消息会保留在这台设备上。', [{ text: '取消', style: 'cancel' }, { text: '退出', style: 'destructive', onPress: () => void logout() }])} style={{ marginTop: spacing.md }} />
        </Card>
      </ScrollView>
    </SafeAreaView>
  );
}

function ReminderRow({ icon, title, value, fallback, onChange }: { icon: keyof typeof Ionicons.glyphMap; title: string; value: string | null; fallback: string; onChange: (value: string | null) => void }) {
  return (
    <View style={styles.reminderRow}>
      <View style={styles.reminderIcon}><Ionicons name={icon} size={18} color={colors.primary} /></View>
      <Text style={styles.reminderTitle}>{title}</Text>
      {value !== null ? <TextInput value={value} onChangeText={onChange} keyboardType="numbers-and-punctuation" maxLength={5} style={styles.timeInput} /> : null}
      <Switch value={value !== null} onValueChange={(enabled) => onChange(enabled ? fallback : null)} trackColor={{ false: colors.line, true: colors.primarySoft }} thumbColor={value !== null ? colors.primary : '#F8F8F8'} />
    </View>
  );
}

function InfoRow({ icon, title, body }: { icon: keyof typeof Ionicons.glyphMap; title: string; body: string }) {
  return <View style={styles.infoRow}><Ionicons name={icon} size={20} color={colors.primary} /><View style={{ flex: 1 }}><Text style={styles.infoTitle}>{title}</Text><Text style={styles.infoBody}>{body}</Text></View></View>;
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  page: { padding: spacing.lg, paddingTop: spacing.xl, paddingBottom: 110 },
  eyebrow: { color: colors.primary, fontSize: 11, fontWeight: '800', letterSpacing: 1.7 },
  title: { color: colors.ink, fontSize: 31, lineHeight: 39, fontWeight: '800', letterSpacing: -1, marginTop: spacing.sm, marginBottom: spacing.xl },
  profileCard: { flexDirection: 'row', alignItems: 'center' },
  avatar: { width: 56, height: 56, borderRadius: 20, backgroundColor: colors.primary, alignItems: 'center', justifyContent: 'center' },
  avatarText: { color: colors.white, fontSize: 23, fontWeight: '800' },
  profileMeta: { marginLeft: spacing.md },
  profileName: { color: colors.ink, fontSize: 18, fontWeight: '800' },
  profileHint: { color: colors.inkMuted, fontSize: 13, marginTop: 4 },
  help: { color: colors.inkMuted, fontSize: 12, marginBottom: spacing.sm },
  input: { backgroundColor: colors.surfaceMuted, borderRadius: radius.md, minHeight: 48, paddingHorizontal: spacing.md, color: colors.ink, fontSize: 16, marginBottom: spacing.md },
  textAction: { color: colors.primary, fontSize: 12, fontWeight: '700' },
  memoryRow: { minHeight: 66, flexDirection: 'row', alignItems: 'center', gap: spacing.md, paddingVertical: spacing.sm },
  rowBorder: { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.line },
  memoryIcon: { width: 36, height: 36, borderRadius: 13, backgroundColor: colors.primarySoft, alignItems: 'center', justifyContent: 'center' },
  goalIcon: { backgroundColor: '#E5F0E6' },
  constraintIcon: { backgroundColor: colors.accentSoft },
  memoryLabelRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  memoryLabel: { color: colors.inkMuted, fontSize: 11 },
  memoryText: { color: colors.ink, fontSize: 14, lineHeight: 20, fontWeight: '600', marginTop: 2 },
  reviewBadge: { color: colors.warning, backgroundColor: colors.accentSoft, fontSize: 9, fontWeight: '700', paddingHorizontal: 6, paddingVertical: 2, borderRadius: radius.pill },
  reminderRow: { minHeight: 62, flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  reminderIcon: { width: 34, height: 34, borderRadius: 12, backgroundColor: colors.primarySoft, alignItems: 'center', justifyContent: 'center' },
  reminderTitle: { color: colors.ink, fontSize: 14, fontWeight: '600', flex: 1 },
  timeInput: { width: 58, color: colors.ink, fontSize: 14, fontWeight: '700', textAlign: 'center', backgroundColor: colors.surfaceMuted, borderRadius: 10, paddingVertical: 7 },
  infoRow: { flexDirection: 'row', gap: spacing.md, paddingVertical: spacing.md },
  infoTitle: { color: colors.ink, fontSize: 14, fontWeight: '700' },
  infoBody: { color: colors.inkMuted, fontSize: 12, lineHeight: 18, marginTop: 3 },
});
