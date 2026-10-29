import React, { useEffect, useRef, useState } from 'react';
import { AccessibilityInfo, ActivityIndicator, Animated, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { brand } from './identity';

const mark = require('../../../assets/brand/cheng-xiao-er-foreground.png');

export function LoadingScreen() {
  const scale = useRef(new Animated.Value(1)).current;
  const [reduceMotion, setReduceMotion] = useState(true);

  useEffect(() => {
    let active = true;
    let changed = false;
    const subscription = AccessibilityInfo.addEventListener('reduceMotionChanged', (value) => {
      changed = true;
      if (active) setReduceMotion(value);
    });
    void AccessibilityInfo.isReduceMotionEnabled().then((value) => {
      if (active && !changed) setReduceMotion(value);
    }).catch(() => undefined);
    return () => { active = false; subscription.remove(); };
  }, []);

  useEffect(() => {
    if (reduceMotion) {
      scale.setValue(1);
      return;
    }
    const pulse = Animated.loop(Animated.sequence([
      Animated.timing(scale, { toValue: 1.035, duration: 1600, useNativeDriver: true, isInteraction: false }),
      Animated.timing(scale, { toValue: 1, duration: 1600, useNativeDriver: true, isInteraction: false }),
    ]));
    pulse.start();
    return () => { pulse.stop(); scale.setValue(1); };
  }, [reduceMotion, scale]);

  return (
    <SafeAreaView style={styles.screen}>
      <View style={styles.hero}>
        <Animated.Image
          accessible={false}
          source={mark}
          resizeMode="contain"
          style={[styles.mark, { transform: [{ scale }] }]}
        />
        <Text style={styles.name}>{brand.name}</Text>
        <Text style={styles.tagline}>{brand.tagline}</Text>
      </View>
      <View style={styles.status} accessibilityRole="progressbar" accessibilityLabel={`正在加载${brand.name}`}>
        <ActivityIndicator size="small" color={brand.colors.inkMuted} animating={!reduceMotion} />
        <Text style={styles.statusText}>正在准备你的记录…</Text>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: brand.colors.background },
  hero: { flex: 1, justifyContent: 'center', alignItems: 'center', paddingHorizontal: 24 },
  mark: { width: 260, height: 260 },
  name: { color: brand.colors.ink, fontSize: 32, fontWeight: '800', marginTop: -28, letterSpacing: 2 },
  tagline: { color: brand.colors.inkMuted, fontSize: 15, lineHeight: 24, marginTop: 16, textAlign: 'center' },
  status: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, paddingHorizontal: 24, paddingTop: 24, paddingBottom: 32 },
  statusText: { color: brand.colors.inkMuted, fontSize: 13 },
});
