import { Ionicons } from '@expo/vector-icons';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import React, { useCallback, useEffect, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { SafeAreaProvider, useSafeAreaInsets } from 'react-native-safe-area-context';

import { LoadingScreen } from './src/shared/brand/LoadingScreen';
import { brand } from './src/shared/brand/identity';
import { AppProvider, useApp } from './src/context/AppContext';
import { AuthScreen } from './src/screens/AuthScreen';
import { CoachScreen } from './src/screens/CoachScreen';
import { CoachProfileScreen } from './src/screens/CoachProfileScreen';
import { ProfileScreen } from './src/screens/ProfileScreen';
import { ProgressScreen } from './src/screens/ProgressScreen';
import { TodayScreen } from './src/screens/TodayScreen';
import { BottomTabBar } from './src/shared/navigation/BottomTabBar';
import { ProfileDetails } from './src/features/user-profile/ProfileDetails';
import type { ProfilePage } from './src/features/user-profile/ProfileDetails';
import type { Tab } from './src/shared/navigation/BottomTabBar';
import { colors, radius, spacing } from './src/theme';

type ProfileGate = { intent: 'coach' | 'edit'; draft: string };

// Reveal the real first React surface, not a timed splash or a network request.
void SplashScreen.preventAutoHideAsync().catch(() => undefined);

export default function App() {
  const reveal = useCallback(() => {
    void SplashScreen.hideAsync().catch(() => undefined);
  }, []);
  return (
    <View style={styles.root} onLayout={reveal}>
      <SafeAreaProvider>
        <AppProvider>
          <StatusBar style="dark" />
          <AppShell />
        </AppProvider>
      </SafeAreaProvider>
    </View>
  );
}

function AppShell() {
  const { booting, authenticated, data, error, clearError } = useApp();
  const [tab, setTab] = useState<Tab>('today');
  const [coachDraft, setCoachDraft] = useState('');
  const [profileGate, setProfileGate] = useState<ProfileGate | null>(null);
  const [profilePage, setProfilePage] = useState<ProfilePage | null>(null);
  const insets = useSafeAreaInsets();
  useEffect(() => {
    setTab('today'); setCoachDraft(''); setProfileGate(null); setProfilePage(null);
  }, [data?.user.id]);

  const openCoach = useCallback((draft = '') => {
    if (!data?.coach_profile.profile) {
      setProfileGate({ intent: 'coach', draft });
      return;
    }
    setCoachDraft(draft);
    setTab('coach');
  }, [data?.coach_profile.profile]);
  const openCoachProfile = useCallback(() => {
    setProfileGate({ intent: 'edit', draft: '' });
  }, []);
  const consumeDraft = useCallback(() => setCoachDraft(''), []);

  if (booting) return <LoadingScreen />;
  if (!authenticated) return <AuthScreen />;
  if (!data) return <LoadingScreen />;

  const activeGate = profileGate ?? (
    tab === 'coach' && !data.coach_profile.profile
      ? { intent: 'coach' as const, draft: coachDraft }
      : null
  );
  if (activeGate) {
    return (
      <CoachProfileScreen
        intent={activeGate.intent}
        onClose={() => {
          setProfileGate(null);
          if (tab === 'coach' && !data.coach_profile.profile) setTab('today');
        }}
        onReady={() => {
          setCoachDraft(activeGate.draft);
          setProfileGate(null);
          setTab('coach');
        }}
      />
    );
  }

  if (profilePage) return <ProfileDetails page={profilePage} onClose={() => setProfilePage(null)}
    openCoach={draft => { setProfilePage(null); openCoach(draft); }} />;

  return (
    <View style={styles.container}>
      <View style={styles.screen}>
        {tab === 'today' ? <TodayScreen openCoach={openCoach} /> : null}
        {tab === 'coach' ? <CoachScreen draft={coachDraft} consumeDraft={consumeDraft} /> : null}
        {tab === 'progress' ? <ProgressScreen openCoach={openCoach} /> : null}
        {tab === 'profile' ? <ProfileScreen openPage={setProfilePage} openCoachProfile={openCoachProfile} /> : null}
      </View>
      {error ? (
        <Pressable onPress={clearError} style={[styles.errorBanner, { bottom: 76 + insets.bottom }]}>
          <Ionicons name="alert-circle" size={18} color={colors.white} />
          <Text numberOfLines={2} style={styles.errorText}>{error}</Text>
          <Ionicons name="close" size={17} color={colors.white} />
        </Pressable>
      ) : null}
      <BottomTabBar selected={tab} bottomInset={insets.bottom} onSelect={next => next === 'coach' ? openCoach() : setTab(next)} />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: brand.colors.background },
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },
  screen: { flex: 1 },
  errorBanner: { position: 'absolute', left: spacing.lg, right: spacing.lg, minHeight: 48, flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingHorizontal: spacing.md, paddingVertical: 10, borderRadius: radius.md, backgroundColor: colors.danger, shadowColor: colors.shadow, shadowOffset: { width: 0, height: 5 }, shadowOpacity: 0.18, shadowRadius: 12, elevation: 8 },
  errorText: { color: colors.white, fontSize: 12, lineHeight: 17, flex: 1, fontWeight: '600' },
});
