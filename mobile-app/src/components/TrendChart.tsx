import React from 'react';
import { StyleSheet, Text, View, useWindowDimensions } from 'react-native';
import Svg, { Circle, Line, Path } from 'react-native-svg';

import { colors } from '../theme';
import type { TrendPoint } from '../types';

export function TrendChart({ points, unit, color = colors.primary }: { points: TrendPoint[]; unit: string; color?: string }) {
  const { width } = useWindowDimensions();
  const chartWidth = Math.max(240, width - 80);
  const chartHeight = 150;
  if (points.length < 2) {
    return (
      <View style={styles.notEnough}>
        <Text style={styles.notEnoughTitle}>再记一次就能看到趋势</Text>
        <Text style={styles.notEnoughText}>趋势不评价你，它只是帮你看清变化。</Text>
      </View>
    );
  }
  const values = points.map((item) => item.value);
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const range = Math.max(maximum - minimum, 1);
  const coordinates = points.map((point, index) => ({
    x: 12 + (index / (points.length - 1)) * (chartWidth - 24),
    y: 14 + ((maximum - point.value) / range) * (chartHeight - 34),
  }));
  const path = coordinates.map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x} ${point.y}`).join(' ');

  return (
    <View>
      <View style={styles.summary}>
        <Text style={styles.latest}>{values.at(-1)?.toFixed(1)} {unit}</Text>
        <Text style={[styles.change, { color: values.at(-1)! <= values[0] ? colors.primary : colors.warning }]}>
          {values.at(-1)! - values[0] > 0 ? '+' : ''}{(values.at(-1)! - values[0]).toFixed(1)} {unit}
        </Text>
      </View>
      <Svg width={chartWidth} height={chartHeight} accessibilityLabel={`${unit}趋势图`}>
        {[25, 70, 115].map((y) => <Line key={y} x1="0" x2={chartWidth} y1={y} y2={y} stroke={colors.line} strokeWidth="1" />)}
        <Path d={path} fill="none" stroke={color} strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
        {coordinates.map((point, index) => (
          <Circle key={points[index].id} cx={point.x} cy={point.y} r={index === coordinates.length - 1 ? 5 : 3} fill={color} />
        ))}
      </Svg>
      <View style={styles.dates}>
        <Text style={styles.date}>{formatDate(points[0].occurred_at)}</Text>
        <Text style={styles.date}>{formatDate(points.at(-1)!.occurred_at)}</Text>
      </View>
    </View>
  );
}

function formatDate(value: string): string {
  const date = new Date(value);
  return `${date.getMonth() + 1}/${date.getDate()}`;
}

const styles = StyleSheet.create({
  notEnough: { minHeight: 160, alignItems: 'center', justifyContent: 'center' },
  notEnoughTitle: { color: colors.ink, fontSize: 16, fontWeight: '700' },
  notEnoughText: { color: colors.inkMuted, marginTop: 5, fontSize: 13 },
  summary: { flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 6 },
  latest: { color: colors.ink, fontSize: 25, fontWeight: '800' },
  change: { fontSize: 14, fontWeight: '700' },
  dates: { flexDirection: 'row', justifyContent: 'space-between', marginTop: -12 },
  date: { color: colors.inkMuted, fontSize: 12 },
});
