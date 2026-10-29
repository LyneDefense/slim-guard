import { Ionicons } from '@expo/vector-icons';
import React, { useEffect } from 'react';
import { BackHandler, KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { appearance as palette } from './appearance';

// Nested settings/editor pages share safe navigation and a reachable, fixed footer.
export function DetailPage({ title, onClose, children, footer, trailing, busy = false, centeredTitle = false }: {
  title: string; onClose: () => void; children: React.ReactNode; footer?: React.ReactNode; trailing?: React.ReactNode; busy?: boolean; centeredTitle?: boolean;
}) {
  useEffect(() => {
    const listener = BackHandler.addEventListener('hardwareBackPress', () => { if (!busy) onClose(); return true; });
    return () => listener.remove();
  }, [onClose, busy]);
  return <SafeAreaView style={styles.safe} edges={['top', 'left', 'right']}>
    <KeyboardAvoidingView style={styles.flex} behavior={Platform.OS === 'ios' ? 'padding' : 'height'}>
      <View style={styles.header}>
        <Pressable disabled={busy} accessibilityRole="button" accessibilityLabel="返回" accessibilityState={{ disabled: busy }} onPress={onClose} style={styles.back}>
          <Ionicons name="chevron-back" size={24} color={palette.ink} />
        </Pressable>
        <Text accessibilityRole="header" style={[styles.title, centeredTitle && styles.centered]}>{title}</Text>
        <View style={styles.trailing}>{trailing}</View>
      </View>
      <ScrollView contentContainerStyle={styles.body} keyboardShouldPersistTaps="handled" keyboardDismissMode="on-drag">
        {children}
      </ScrollView>
      <SafeAreaView edges={['bottom']} style={[styles.footer, !footer && styles.noFooter]}>{footer}</SafeAreaView>
    </KeyboardAvoidingView>
  </SafeAreaView>;
}

const styles = StyleSheet.create({
  flex: { flex: 1 }, safe: { flex: 1, backgroundColor: palette.background },
  header: { minHeight: 52, paddingHorizontal: 8, flexDirection: 'row', alignItems: 'center', gap: 6 },
  back: { width: 44, height: 44, alignItems: 'center', justifyContent: 'center' },
  title: { color: palette.ink, fontSize: 18, lineHeight: 26, fontWeight: '700', flex: 1 },
  centered: { textAlign: 'center' }, trailing: { minWidth: 44, alignItems: 'flex-end', paddingRight: 8 },
  body: { padding: 16, paddingTop: 8, gap: 12 },
  footer: { backgroundColor: palette.surface, paddingHorizontal: 16, paddingTop: 10, paddingBottom: 8, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: palette.line },
  noFooter: { backgroundColor: palette.background, borderTopWidth: 0, paddingTop: 0 },
});
