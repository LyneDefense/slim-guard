import React from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { appearance as palette } from '../ui/appearance';
import { BrandMark } from './BrandMark';

export function BrandHeader({ status }: { status: string }) {
  return <View style={styles.header}>
    <BrandMark compact />
    <View style={styles.status} accessibilityLiveRegion="polite">
      <View style={styles.dot} /><Text style={styles.statusText}>{status}</Text>
    </View>
  </View>;
}

const styles = StyleSheet.create({
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8, paddingHorizontal: 16, paddingTop: 10, paddingBottom: 17, backgroundColor: palette.background },
  status: { borderRadius: 24, paddingHorizontal: 10, paddingVertical: 6, flexDirection: 'row', alignItems: 'center', gap: 6, backgroundColor: '#FFF0E6', flexShrink: 1 },
  dot: { width: 7, height: 7, borderRadius: 4, backgroundColor: palette.orange },
  statusText: { color: palette.orangeInk, fontSize: 12, lineHeight: 16, fontWeight: '500', flexShrink: 1 },
});
