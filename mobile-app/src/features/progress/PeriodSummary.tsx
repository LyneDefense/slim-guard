import React from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { appearance as palette } from '../../shared/ui/appearance';
import { TonalBackdrop } from '../../shared/ui/TonalBackdrop';
import { measurementLabels, periodChange } from './presentation';
import type { DailyMeasurement, MeasurementMetric } from './types';

export function PeriodSummary({ points, metric }: { points: DailyMeasurement[]; metric: MeasurementMetric }) {
  const { unit } = measurementLabels[metric];
  return <View style={styles.card}>
    <TonalBackdrop tone="indigo" />
    <View style={styles.values}>
      <SummaryValue label="本期首次" value={points[0]?.value} unit={unit} />
      <View style={styles.divider} />
      <SummaryValue label="最近测量" value={points[points.length - 1]?.value} unit={unit} />
    </View>
    <View style={styles.footer}>
      <View style={styles.change}>
        {points.length > 1 && <Text style={styles.changeLabel}>本期首末变化</Text>}
        <Text style={styles.changeValue}>{periodChange(points, metric)}</Text>
      </View>
    </View>
  </View>;
}

function SummaryValue({ label, value, unit }: { label: string; value?: number; unit: string }) {
  return <View style={styles.column} accessible accessibilityLabel={`${label}，${value === undefined ? '暂无测量' : `${value.toFixed(1)} ${unit}`}`}>
    <Text style={styles.label}>{label}</Text>
    <View style={styles.valueRow}>
      <Text adjustsFontSizeToFit minimumFontScale={0.6} numberOfLines={1} style={styles.value}>{value === undefined ? '—' : value.toFixed(1)}</Text>
      <Text style={styles.unit}>{unit}</Text>
    </View>
  </View>;
}

const styles = StyleSheet.create({
  card: { backgroundColor: palette.indigo, borderRadius: 18, paddingHorizontal: 20, paddingTop: 16, paddingBottom: 10, overflow: 'hidden', marginBottom: 12 },
  values: { flexDirection: 'row', alignItems: 'center', gap: 24 }, column: { flex: 1, minWidth: 0 },
  divider: { width: StyleSheet.hairlineWidth, height: 64, backgroundColor: 'rgba(255,255,255,0.35)' },
  label: { color: palette.surface, fontSize: 13, lineHeight: 20 },
  valueRow: { flexDirection: 'row', alignItems: 'baseline', gap: 4, marginTop: 2 },
  value: { flexShrink: 1, fontSize: 42, lineHeight: 49, fontWeight: '700', letterSpacing: -1.4, color: palette.surface },
  unit: { color: palette.surface, fontSize: 15, fontWeight: '600' },
  footer: { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: 'rgba(255,255,255,0.25)', marginTop: 9, paddingTop: 9 },
  change: { alignSelf: 'flex-start', flexDirection: 'row', flexWrap: 'wrap', alignItems: 'baseline', gap: 10, paddingHorizontal: 12, paddingVertical: 5, borderRadius: 24, backgroundColor: palette.apricot },
  changeLabel: { color: palette.orangeInk, fontSize: 11, lineHeight: 18, fontWeight: '600' },
  changeValue: { color: palette.orangeInk, fontSize: 16, lineHeight: 21, fontWeight: '700' },
});
