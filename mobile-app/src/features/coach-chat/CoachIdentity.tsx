import React from 'react';
import { Image, StyleSheet, Text, View } from 'react-native';

import { appearance as palette } from '../../shared/ui/appearance';

export const coachIdentity = { name: '医生教练', tagline: '帮你记着，也帮你盯着。' } as const;
const avatar = require('../../../assets/coach/doctor-avatar.png');

export function CoachAvatar({ size = 38 }: { size?: number }) {
  return <Image accessible={false} source={avatar} resizeMode="contain" style={{ width: size, height: size, borderRadius: size / 2 }} />;
}

export function CoachIdentity() {
  return <View style={styles.identity}>
    <CoachAvatar size={52} />
    <View style={styles.copy}><Text style={styles.name}>{coachIdentity.name}</Text><Text style={styles.tagline}>{coachIdentity.tagline}</Text></View>
  </View>;
}

const styles = StyleSheet.create({
  identity: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16, paddingTop: 1, paddingBottom: 14, gap: 14 },
  copy: { flex: 1 }, name: { color: palette.ink, fontSize: 17, lineHeight: 25, fontWeight: '700' },
  tagline: { color: '#81889D', fontSize: 12, lineHeight: 19, marginTop: 2 },
});
