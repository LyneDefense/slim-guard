import { Ionicons } from '@expo/vector-icons';
import React, { useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import { AppIcon } from '../../shared/ui/AppIcon';
import { appearance as palette } from '../../shared/ui/appearance';
import { MealEditor } from './MealEditor';
import { RecordRow } from './RecordRow';
import type { RecordKind } from './types';
import { useRecordSection } from './useRecordSection';

export function RecordHistory({ timezone, refreshKey, onChanged }: {
  timezone: string; refreshKey: unknown; onChanged: () => void;
}) {
  const [expanded, setExpanded] = useState({ meal: false, exercise: false });
  const joined = !expanded.meal && !expanded.exercise;
  return <View style={styles.history}>
    <Text accessibilityRole="header" style={[styles.heading, !joined && styles.expandedHeading]}>记录历史</Text>
    <View style={[styles.sections, joined && styles.joined]}>
      {(['meal', 'exercise'] as const).map(kind => <RecordSection key={kind} kind={kind}
        title={kind === 'meal' ? '饮食记录' : '运动记录'}
        timezone={timezone} refreshKey={refreshKey} onChanged={kind === 'meal' ? onChanged : undefined}
        expanded={expanded[kind]} joined={joined} onToggle={() => setExpanded(previous => ({ ...previous, [kind]: !previous[kind] }))} />)}
    </View>
  </View>;
}

function RecordSection({ kind, title, timezone, refreshKey, onChanged, expanded, onToggle, joined }: {
  kind: RecordKind; title: string; timezone: string; refreshKey: unknown; onChanged?: () => void;
  expanded: boolean; onToggle: () => void; joined: boolean;
}) {
  const state = useRecordSection(kind, timezone, refreshKey, expanded, onChanged);
  return <View style={[styles.section, !joined && styles.separate]}>
    <Pressable accessibilityRole="button" accessibilityLabel={title} accessibilityState={{ expanded }} onPress={onToggle}
      style={[styles.header, expanded && styles.expandedHeader, joined && kind === 'meal' && styles.divider]}>
      {expanded && <View style={[styles.tile, { backgroundColor: kind === 'meal' ? palette.lavender : palette.gold }]}>
        <AppIcon name={kind === 'meal' ? 'meal' : 'exercise'} size={28} color={kind === 'meal' ? palette.ink : palette.orange} />
      </View>}
      <Text style={[styles.title, expanded && styles.expandedTitle]}>{title}</Text>
      <Text style={styles.hint}>{expanded ? '收起' : '展开'}</Text>
      <Ionicons name={expanded ? 'chevron-up' : 'chevron-forward'} size={17} color={palette.muted} />
    </Pressable>
    {expanded && <View>
      {state.page?.records.map(record => <RecordRow key={record.ref.record_id} record={record} timezone={timezone}
        onEdit={kind === 'meal' ? () => void state.openEditor(record) : undefined} opening={state.opening === record.ref.record_id} />)}
      {state.page?.records.length === 0 && <Text style={styles.empty}>暂无记录</Text>}
      {state.error && <Pressable accessibilityRole="button" onPress={() => void state.retry()} style={styles.feedback}><Text style={styles.hint}>{state.error}</Text></Pressable>}
      {state.busy ? <ActivityIndicator style={styles.feedback} color={palette.indigo} /> : !state.error && state.page?.has_more &&
        <Pressable accessibilityRole="button" accessibilityLabel={`加载更早的${title}`} onPress={() => void state.load(state.page?.next_cursor || undefined)} style={styles.more}>
          <Text style={styles.hint}>加载更早</Text><Ionicons name="chevron-down" size={16} color={palette.muted} />
        </Pressable>}
    </View>}
    {state.editing && <MealEditor key={state.editing.ref.record_id} record={state.editing} onClose={state.closeEditor} onSaved={state.saved} />}
  </View>;
}

const styles = StyleSheet.create({
  history: { marginTop: 22 },
  heading: { color: palette.ink, fontSize: 19, lineHeight: 27, fontWeight: '700', marginBottom: 10 },
  expandedHeading: { fontSize: 24, lineHeight: 32, marginBottom: 14 },
  sections: { gap: 14 },
  joined: { ...palette.shadow, backgroundColor: palette.surface, borderRadius: 18, paddingHorizontal: 15, gap: 0 },
  section: { minWidth: 0 },
  separate: { ...palette.shadow, backgroundColor: palette.surface, borderRadius: 18, paddingHorizontal: 15, paddingTop: 10, paddingBottom: 4 },
  header: { flexDirection: 'row', alignItems: 'center', gap: 9, minHeight: 56, paddingVertical: 8 },
  expandedHeader: { minHeight: 44, paddingVertical: 0 },
  divider: { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: palette.line },
  title: { color: palette.ink, fontSize: 15, lineHeight: 23, fontWeight: '600', flex: 1 },
  expandedTitle: { fontSize: 18, fontWeight: '700', marginLeft: 3 },
  tile: { width: 44, height: 44, borderRadius: 14, alignItems: 'center', justifyContent: 'center', marginRight: 1 },
  hint: { color: palette.muted, fontSize: 13, lineHeight: 20 },
  more: { minHeight: 44, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 9 },
  feedback: { paddingVertical: 14, minHeight: 44 },
  empty: { color: palette.muted, fontSize: 14, paddingVertical: 18, textAlign: 'center' },
});
