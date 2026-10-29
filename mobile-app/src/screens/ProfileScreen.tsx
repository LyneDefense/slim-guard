import React from 'react';
import { RefreshControl, ScrollView, StyleSheet, Text } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { useApp } from '../context/AppContext';
import type { ProfilePage } from '../features/user-profile/ProfileDetails';
import { ProfileHealthCard } from '../features/user-profile/ProfileHealthCard';
import { ProfileMenu } from '../features/user-profile/ProfileMenu';
import { bindingLabel, useWeComBinding } from '../features/user-profile/useWeComBinding';
import { BrandHeader } from '../shared/brand/BrandHeader';
import { appearance as palette } from '../shared/ui/appearance';

export function ProfileScreen({ openPage, openCoachProfile }: {
  openPage: (page: ProfilePage) => void; openCoachProfile: () => void;
}) {
  const { data, online, pending, loading, refresh, getWeComBinding, createWeComBinding } = useApp();
  const connection = useWeComBinding(getWeComBinding, createWeComBinding);
  if (!data) return null;
  const reminderCount = [data.routine.weight_reminder_time, data.routine.meal_reminder_time, data.routine.daily_review_time].filter(value => value !== null).length;
  return <SafeAreaView style={styles.safe} edges={['top']}>
    <BrandHeader status={online ? '已同步' : pending.length ? `${pending.length} 条待发送` : '未连接'} />
    <ScrollView contentContainerStyle={styles.page}
      refreshControl={<RefreshControl refreshing={loading} onRefresh={() => { void refresh(); void connection.request(); }} tintColor={palette.indigo} colors={[palette.indigo]} />}>
      <ProfileHealthCard user={data.user} profile={data.coach_profile.profile} onEdit={openCoachProfile} />
      <ProfileMenu reminderCount={reminderCount} connectionLabel={bindingLabel(connection.binding, connection.busy, connection.error)} onOpen={openPage} />
      <Text style={styles.footnote}>按自己的节奏，慢慢来。</Text>
    </ScrollView>
  </SafeAreaView>;
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: palette.background }, page: { paddingHorizontal: 16, paddingBottom: 20 },
  footnote: { color: palette.muted, fontSize: 11, lineHeight: 18, textAlign: 'center', marginTop: 18 },
});
