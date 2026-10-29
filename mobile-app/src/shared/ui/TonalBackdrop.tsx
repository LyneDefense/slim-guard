import React, { useId } from 'react';
import { StyleSheet, View } from 'react-native';
import Svg, { Defs, LinearGradient, Path, Rect, Stop } from 'react-native-svg';

// Decorative only: home metrics and period summaries share the same soft arcs.
export function TonalBackdrop({ tone }: { tone: 'indigo' | 'apricot' }) {
  const gradient = useId();
  const indigo = tone === 'indigo';
  return <View pointerEvents="none" style={StyleSheet.absoluteFill}>
    <Svg width="100%" height="100%" viewBox="0 0 180 140" preserveAspectRatio="none" accessible={false}>
      <Defs><LinearGradient id={gradient} x1="0" y1="0" x2="1" y2="1">
        <Stop offset="0" stopColor={indigo ? '#4B588E' : '#FCC59F'} /><Stop offset="1" stopColor={indigo ? '#475383' : '#FAB58A'} />
      </LinearGradient></Defs>
      <Rect width="180" height="140" fill={`url(#${gradient})`} />
      <Path d="M180 32C126 48 115 88 129 140H180Z" fill={indigo ? '#8891BC' : '#EC955F'} opacity={indigo ? 0.22 : 0.2} />
      <Path d="M180 91c-35 3-49 19-60 49h60Z" fill={indigo ? '#344171' : '#F7B48C'} opacity="0.48" />
    </Svg>
  </View>;
}
