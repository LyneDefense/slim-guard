import { Ionicons } from '@expo/vector-icons';
import { StatusBar } from 'expo-status-bar';
import React, { useCallback, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { SafeAreaProvider, useSafeAreaInsets } from 'react-native-safe-area-context';

import { LoadingScreen } from './src/components/ui';
import { AppProvider, useApp } from './src/context/AppContext';
import { AuthScreen } from './src/screens/AuthScreen';
import { CoachScreen } from './src/screens/CoachScreen';
import { ProfileScreen } from './src/screens/ProfileScreen';
import { ProgressScreen } from './src/screens/ProgressScreen';
import { TodayScreen } from './src/screens/TodayScreen';
import { colors, radius, spacing } from './src/theme';

type Tab = 'today' | 'coach' | 'progress' | 'profile';

const tabs: Array<{ id: Tab; title: string; icon: keyof typeof Ionicons.glyphMap; activeIcon: keyof typeof Ionicons.glyphMap }> = [
  { id: 'today', title: '今天', icon: 'sunny-outline', activeIcon: 'sunny' },
  { id: 'coach', title: '教练', icon: 'chatbubble-ellipses-outline', activeIcon: 'chatbubble-ellipses' },
  { id: 'progress', title: '趋势', icon: 'analytics-outline', activeIcon: 'analytics' },
  { id: 'profile', title: '我的', icon: 'person-outline', activeIcon: 'person' },
];

export default function App() {
  return (
    <SafeAreaProvider>
      <AppProvider>
        <StatusBar style="dark" />
        <AppShell />
      </AppProvider>
    </SafeAreaProvider>
  );
}

function AppShell() {
  const { booting, authenticated, data, error, clearError } = useApp();
  const [tab, setTab] = useState<Tab>('today');
  const [coachDraft, setCoachDraft] = useState('');
  const insets = useSafeAreaInsets();

  const openCoach = useCallback((draft = '') => {
    setCoachDraft(draft);
    setTab('coach');
  }, []);
  const consumeDraft = useCallback(() => setCoachDraft(''), []);

  if (booting) return <LoadingScreen />;
  if (!authenticated) return <AuthScreen />;
  if (!data) return <LoadingScreen />;

  return (
    <View style={styles.container}>
      <View style={styles.screen}>
        {tab === 'today' ? <TodayScreen openCoach={openCoach} /> : null}
        {tab === 'coach' ? <CoachScreen draft={coachDraft} consumeDraft={consumeDraft} /> : null}
        {tab === 'progress' ? <ProgressScreen openCoach={openCoach} /> : null}
        {tab === 'profile' ? <ProfileScreen openCoach={openCoach} /> : null}
      </View>
      {error ? (
        <Pressable onPress={clearError} style={[styles.errorBanner, { bottom: 76 + insets.bottom }]}>
          <Ionicons name="alert-circle" size={18} color={colors.white} />
          <Text numberOfLines={2} style={styles.errorText}>{error}</Text>
          <Ionicons name="close" size={17} color={colors.white} />
        </Pressable>
      ) : null}
      <View style={[styles.tabBar, { paddingBottom: Math.max(insets.bottom, 8) }]}>
        {tabs.map((item) => {
          const active = tab === item.id;
          return (
            <Pressable key={item.id} accessibilityRole="tab" accessibilityState={{ selected: active }} onPress={() => setTab(item.id)} style={styles.tab}>
              <View style={[styles.tabIcon, active && styles.tabIconActive]}><Ionicons name={active ? item.activeIcon : item.icon} size={21} color={active ? colors.white : colors.inkMuted} /></View>
              <Text style={[styles.tabText, active && styles.tabTextActive]}>{item.title}</Text>
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },
  screen: { flex: 1 },
  tabBar: { flexDirection: 'row', paddingTop: 8, paddingHorizontal: spacing.md, backgroundColor: colors.surface, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.line },
  tab: { flex: 1, alignItems: 'center', gap: 3 },
  tabIcon: { width: 34, height: 30, borderRadius: 13, alignItems: 'center', justifyContent: 'center' },
  tabIconActive: { backgroundColor: colors.primary },
  tabText: { color: colors.inkMuted, fontSize: 10, fontWeight: '600' },
  tabTextActive: { color: colors.primary, fontWeight: '800' },
  errorBanner: { position: 'absolute', left: spacing.lg, right: spacing.lg, minHeight: 48, flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingHorizontal: spacing.md, paddingVertical: 10, borderRadius: radius.md, backgroundColor: colors.danger, shadowColor: colors.shadow, shadowOffset: { width: 0, height: 5 }, shadowOpacity: 0.18, shadowRadius: 12, elevation: 8 },
  errorText: { color: colors.white, fontSize: 12, lineHeight: 17, flex: 1, fontWeight: '600' },
});
