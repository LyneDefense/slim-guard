import React from 'react';
import { Image, StyleSheet, Text, View } from 'react-native';

import { brand } from './identity';

const icon = require('../../../assets/brand/cheng-xiao-er-icon.png');

export function BrandIcon({ size = 46 }: { size?: number }) {
  return (
    <Image
      accessible={false}
      source={icon}
      resizeMode="contain"
      style={{ width: size, height: size, borderRadius: size * 0.28 }}
    />
  );
}

export function BrandMark({ compact = false }: { compact?: boolean }) {
  return (
    <View style={styles.row}>
      <BrandIcon size={compact ? 36 : 46} />
      <Text style={[styles.name, compact && styles.compact]}>{brand.name}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  name: { color: brand.colors.ink, fontSize: 26, fontWeight: '800' },
  compact: { fontSize: 22 },
});
