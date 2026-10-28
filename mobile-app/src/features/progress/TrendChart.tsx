import React from 'react';
import { StyleSheet, Text, View, useWindowDimensions } from 'react-native';
import Svg, { Circle, Line, Path } from 'react-native-svg';

import { colors } from '../../theme';
import type { DailyMeasurement } from './types';

type Props = {
  points: DailyMeasurement[];
  fromDate: string;
  toDate: string;
  unit: string;
  color?: string;
};

export function TrendChart({ points, fromDate, toDate, unit, color = colors.primary }: Props) {
  const { width } = useWindowDimensions();
  const chartWidth = Math.max(240, width - 80);
  const chartHeight = 150;
  if (points.length === 0) {
    return (
      <View style={styles.notEnough}>
        <Text style={styles.notEnoughTitle}>这个周期还没有{unit === 'kg' ? '体重' : '体脂'}记录</Text>
        <Text style={styles.notEnoughText}>可以补一条记录，或切换统计周期查看。</Text>
      </View>
    );
  }
  const values = points.map((item) => item.value);
  const latest = values[values.length - 1];
  const change = latest - values[0];
  const changeLabel = points.length < 2
    ? '本周期仅记录一天'
    : `${change > 0 ? '+' : ''}${change.toFixed(1)} ${unit}`;
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const range = Math.max(maximum - minimum, 1);
  // UTC is only a stable coordinate for calendar dates, not a fabricated measurement time.
  const day = (value: string) => Date.parse(`${value}T00:00:00Z`);
  const start = day(fromDate);
  const duration = Math.max(day(toDate) - start, 86400000);
  const coordinates = points.map(point => ({
    x: 12 + ((day(point.on) - start) / duration) * (chartWidth - 24),
    y: 14 + ((maximum - point.value) / range) * (chartHeight - 34),
  }));
  const path = coordinates.map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x} ${point.y}`).join(' ');

  return (
    <View>
      <View style={styles.summary}>
        <Text style={styles.latest}>{latest.toFixed(1)} {unit}</Text>
        <Text style={[styles.change, { color: change <= 0 ? colors.primary : colors.warning }]}>
          {changeLabel}
        </Text>
      </View>
      <Svg width={chartWidth} height={chartHeight} accessibilityLabel={`${unit}趋势图`}>
        {[25, 70, 115].map((y) => <Line key={y} x1="0" x2={chartWidth} y1={y} y2={y} stroke={colors.line} strokeWidth="1" />)}
        <Path d={path} fill="none" stroke={color} strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
        {coordinates.map((point, index) => (
          <Circle key={points[index].record_id} cx={point.x} cy={point.y} r={index === coordinates.length - 1 ? 5 : 3} fill={color} />
        ))}
      </Svg>
      <View style={styles.dates}>
        <Text style={styles.date}>{formatDate(fromDate)}</Text>
        <Text style={styles.date}>{formatDate(toDate)}</Text>
      </View>
    </View>
  );
}

function formatDate(value: string): string {
  return value.slice(5).replace('-', '/');
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
