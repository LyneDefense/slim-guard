import { Ionicons } from '@expo/vector-icons';
import React, { useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import { appearance as palette } from '../../shared/ui/appearance';
import type { DailyInsights, InsightEvidence } from './types';

export function evidenceLabel(item: InsightEvidence): string {
  const detail = item.data.content;
  if (item.kind !== 'exercise') return item.label;
  const parts = [item.label];
  if (detail?.duration_minutes != null) parts.push(`${detail.duration_minutes} 分钟`);
  if (detail?.steps != null) parts.push(`${detail.steps.toLocaleString()} 步`);
  return parts.join(' · ');
}

export function DailySummaryCard({ view, busy, verifying, error, onRetry }: {
  view: DailyInsights | null; busy: boolean; verifying: boolean; error: string | null;
  onRetry: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  useEffect(() => { setExpanded(false); }, [view?.generated_at, view?.date, verifying]);
  const ready = !verifying && !error && view?.summary_state === 'ready' && !!view.summary;
  const empty = !verifying && !error && view?.summary_state === 'empty';
  const unavailable = view?.summary_state === 'unavailable';
  const failed = !!error || view?.summary_state === 'failed' || unavailable;
  let time = '';
  if (ready && view?.as_of && Number.isFinite(new Date(view.as_of).getTime())) {
    time = new Date(view.as_of).toLocaleTimeString('zh-CN', { timeZone: view.timezone,
      hour: '2-digit', minute: '2-digit', hour12: false });
  }
  return <View style={styles.card}>
    <View style={styles.heading}>
      <View style={styles.icon}><Ionicons name="document-text-outline" size={20} color={palette.indigo} /></View>
      <Text accessibilityRole="header" style={styles.title}>今日总结</Text>
      {time ? <Text style={styles.time}>截至 {time}</Text> : null}
    </View>
    {ready ? <Text style={styles.body}>{view!.summary}</Text>
      : empty ? <Text style={styles.hint}>今天还没有可回顾的记录。</Text>
      : failed ? <View style={styles.stateRow}><Text style={styles.hint}>{unavailable ? '今日总结暂不可用' : error || '今日总结暂未生成'}</Text>
        <Pressable accessibilityRole="button" accessibilityLabel="重新生成今日总结" disabled={busy} onPress={onRetry} style={styles.retry}>
          <Text style={styles.link}>{busy ? '加载中' : '重试'}</Text>
        </Pressable></View>
      : <View style={styles.stateRow}><ActivityIndicator size="small" color={palette.indigo} />
        <Text style={styles.hint}>{verifying ? '正在核对最新记录…' : '教练正在整理今天的记录…'}</Text></View>}
    {ready && !!view?.evidence.length && <>
      <Pressable accessibilityRole="button" accessibilityLabel="查看今日总结依据" accessibilityState={{ expanded }}
        style={styles.disclosure} onPress={() => setExpanded(value => !value)}>
        <Text style={styles.link}>{expanded ? '收起依据' : '查看依据'}</Text>
        <Ionicons name={expanded ? 'chevron-up' : 'chevron-down'} size={14} color={palette.muted} />
      </Pressable>
      {expanded && <View style={styles.evidence}>{view.evidence.map(item => <View key={item.id} style={styles.evidenceRow}>
        <View style={styles.dot} /><Text style={styles.evidenceText}>{evidenceLabel(item)}</Text>
      </View>)}</View>}
    </>}
  </View>;
}

const styles = StyleSheet.create({
  card: { ...palette.shadow, backgroundColor: palette.surface, borderRadius: 18, padding: 16, marginTop: 22 },
  heading: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 12 },
  icon: { width: 32, height: 32, borderRadius: 10, backgroundColor: palette.lavender, alignItems: 'center', justifyContent: 'center' },
  title: { fontSize: 17, lineHeight: 24, fontWeight: '700', color: palette.ink, flex: 1 },
  time: { fontSize: 11, lineHeight: 17, color: palette.muted },
  body: { fontSize: 14, lineHeight: 23, color: palette.ink },
  hint: { fontSize: 13, lineHeight: 21, color: palette.muted, flexShrink: 1 },
  stateRow: { minHeight: 42, flexDirection: 'row', alignItems: 'center', gap: 8 },
  retry: { minHeight: 44, minWidth: 44, marginLeft: 'auto', alignItems: 'center', justifyContent: 'center' },
  link: { color: palette.indigo, fontSize: 12, lineHeight: 18, fontWeight: '600' },
  disclosure: { minHeight: 44, marginBottom: -6, flexDirection: 'row', alignItems: 'center', gap: 6 },
  evidence: { borderTopWidth: StyleSheet.hairlineWidth, borderColor: palette.line, paddingTop: 10, gap: 8 },
  evidenceRow: { flexDirection: 'row', gap: 8, alignItems: 'flex-start' },
  dot: { width: 5, height: 5, borderRadius: 3, backgroundColor: palette.indigo, marginTop: 7 },
  evidenceText: { fontSize: 12, lineHeight: 19, color: palette.muted, flex: 1 },
});
