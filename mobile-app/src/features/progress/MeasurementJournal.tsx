import { Ionicons } from '@expo/vector-icons';
import React, { useRef, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import type { ScrollViewInstance } from 'react-native';

import { appearance as palette } from '../../shared/ui/appearance';
import { MeasurementDetail } from './MeasurementDetail';
import { calendarCaption, measurementLabels, periodDates, shortDate, weekday } from './presentation';
import type { DailyMeasurement, MeasurementMetric } from './types';

export function MeasurementJournal({ metric, onMetricChange, points, fromDate, toDate, openCoach }: {
  metric: MeasurementMetric; onMetricChange: (value: MeasurementMetric) => void; points: DailyMeasurement[];
  fromDate: string; toDate: string; openCoach: (draft?: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [selected, setSelected] = useState(toDate);
  const [detailDate, setDetailDate] = useState<string | null>(null);
  const ribbon = useRef<ScrollViewInstance>(null);
  const { label, unit, draft } = measurementLabels[metric];
  const byDay = new Map(points.map(point => [point.on, point]));
  const dates = periodDates(fromDate, toDate);
  const newestFirst = points.slice().reverse();
  const shown = expanded ? newestFirst : newestFirst.slice(0, 3);
  function inspect(on: string) { setSelected(on); setDetailDate(on); }

  return <View style={styles.card}>
    <View style={styles.header}>
      <View style={styles.tabs} accessibilityRole="tablist">
        {(['weight', 'bodyFat'] as const).map(value => <Pressable key={value} accessibilityRole="tab"
          accessibilityLabel={measurementLabels[value].label} accessibilityState={{ selected: value === metric }} onPress={() => onMetricChange(value)}
          hitSlop={6} style={[styles.tab, value === metric && styles.tabSelected]}>
          <Text style={[styles.tabText, value === metric && styles.tabTextSelected]}>{measurementLabels[value].label}</Text>
        </Pressable>)}
      </View>
      <Pressable accessibilityRole="button" accessibilityLabel={`记一笔${label}`} hitSlop={6} onPress={() => openCoach(draft)} style={styles.add}>
        <Ionicons name="add-circle" size={21} color={palette.orange} /><Text style={styles.addText}>记一笔</Text>
      </Pressable>
    </View>
    <Text style={styles.caption}>{calendarCaption(fromDate, toDate)}</Text>
    <ScrollView horizontal ref={ribbon} showsHorizontalScrollIndicator={false} contentContainerStyle={styles.days}
      onContentSizeChange={() => ribbon.current?.scrollToEnd({ animated: false })}>
      {dates.map(on => <Pressable key={on} accessibilityRole="button"
        accessibilityLabel={`${on} ${weekday(on)}，${byDay.has(on) ? `有${label}测量` : '未测量'}`}
        accessibilityState={{ selected: selected === on }} onPress={() => inspect(on)} style={[styles.day, selected === on && styles.daySelected]}>
        <Text style={[styles.weekday, selected === on && styles.selectedText]}>{weekday(on)}</Text>
        <Text style={[styles.dayNumber, selected === on && styles.selectedText]}>{Number(on.slice(-2))}</Text>
        <View style={[styles.dot, !byDay.has(on) && styles.dotEmpty]} />
      </Pressable>)}
    </ScrollView>
    {dates.length > 7 && <Text style={styles.scrollHint}>左右滑动查看日期</Text>}
    {!points.length && <View style={styles.empty}>
      <Text style={styles.emptyTitle}>这段时间还没有{label}记录</Text>
      <Text style={styles.emptyHint}>{dates.length > 7 ? '从一次测量开始，慢慢记录就好。' : '可以记一笔，或切换到近 30 天看看。'}</Text>
    </View>}
    {shown.map(point => <Pressable key={point.record_id} accessibilityRole="button"
      accessibilityLabel={`查看 ${point.on} ${label} ${point.value.toFixed(1)} ${unit}`} onPress={() => inspect(point.on)} style={styles.row}>
      <Text style={styles.rowDate}>{shortDate(point.on)}</Text><Text style={styles.rowWeekday}>{weekday(point.on)}</Text>
      <View style={styles.rowLabel}><View style={styles.rowDot} /><Text style={styles.rowLabelText}>{label}</Text></View>
      <Text style={styles.rowValue}>{point.value.toFixed(1)}<Text style={styles.unit}> {unit}</Text></Text>
      <Ionicons name="chevron-forward" size={17} color={palette.muted} />
    </Pressable>)}
    {points.length > 3 && <Pressable accessibilityRole="button" accessibilityLabel={expanded ? '收起测量明细' : `查看全部 ${points.length} 天的测量`}
      onPress={() => setExpanded(value => !value)} style={styles.more}>
      <Text style={styles.moreText}>{expanded ? '收起明细' : `查看全部 ${points.length} 天`}</Text>
      <Ionicons name={expanded ? 'chevron-up' : 'chevron-down'} size={17} color={palette.muted} />
    </Pressable>}
    {detailDate && <MeasurementDetail on={detailDate} point={byDay.get(detailDate)} metric={metric} onClose={() => setDetailDate(null)} openCoach={openCoach} />}
  </View>;
}

const styles = StyleSheet.create({
  card: { ...palette.shadow, borderRadius: 18, backgroundColor: palette.surface, paddingHorizontal: 12, paddingTop: 10, paddingBottom: 2 },
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  tabs: { flexDirection: 'row', gap: 7 }, tab: { minWidth: 70, minHeight: 32, justifyContent: 'center', alignItems: 'center', paddingHorizontal: 12, borderRadius: 12 },
  tabSelected: { backgroundColor: palette.lavender }, tabText: { fontSize: 17, color: palette.muted }, tabTextSelected: { color: palette.ink, fontWeight: '700' },
  add: { minHeight: 32, flexDirection: 'row', alignItems: 'center', gap: 5 }, addText: { color: palette.orange, fontSize: 14, fontWeight: '600' },
  caption: { fontSize: 11, lineHeight: 18, color: palette.muted, marginTop: 3, marginBottom: 4 },
  days: { flexGrow: 1, paddingBottom: 8, gap: 2 }, day: { minWidth: 44, minHeight: 48, flex: 1, borderRadius: 12, paddingHorizontal: 4, paddingVertical: 4, alignItems: 'center', gap: 2 },
  daySelected: { backgroundColor: palette.peach }, weekday: { fontSize: 10, lineHeight: 13, color: palette.muted },
  dayNumber: { fontSize: 13, lineHeight: 16, fontWeight: '600', color: palette.muted }, selectedText: { color: palette.orange },
  dot: { width: 7, height: 7, borderRadius: 4, backgroundColor: palette.indigo, marginTop: 1 },
  dotEmpty: { backgroundColor: palette.surface, borderWidth: 1, borderColor: '#9B9FB0' }, scrollHint: { color: palette.muted, fontSize: 10, marginBottom: 8 },
  row: { flexDirection: 'row', alignItems: 'center', minHeight: 44, paddingVertical: 8, gap: 8, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: palette.line },
  rowDate: { color: palette.ink, fontSize: 13, fontWeight: '500' }, rowWeekday: { color: palette.muted, fontSize: 11 },
  rowLabel: { flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8 }, rowDot: { width: 7, height: 7, borderRadius: 4, backgroundColor: palette.indigo },
  rowLabelText: { color: palette.muted, fontSize: 11 }, rowValue: { color: palette.ink, fontSize: 20, fontWeight: '700' }, unit: { fontSize: 12, color: palette.muted, fontWeight: '500' },
  more: { minHeight: 44, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: palette.line },
  moreText: { color: palette.ink, fontSize: 13, fontWeight: '500' }, empty: { paddingVertical: 28, alignItems: 'center', gap: 8 },
  emptyTitle: { color: palette.ink, fontSize: 14, fontWeight: '600' }, emptyHint: { color: palette.muted, fontSize: 12, lineHeight: 20 },
});
