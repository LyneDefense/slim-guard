import { Ionicons } from '@expo/vector-icons';
import React, { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { useApp } from '../../context/AppContext';
import { DetailPage } from '../../shared/ui/DetailPage';
import { appearance as palette } from '../../shared/ui/appearance';
import { SettingsAction, settingsStyles } from '../../shared/ui/SettingsPrimitives';
import type { MemoryItem } from '../../types';
import { memoryGroup, memoryGroups, memoryPresentation } from './presentation';
import type { MemoryGroup } from './presentation';

export function CoachMemoryScreen({ onClose, openCoach }: { onClose: () => void; openCoach: (draft?: string) => void }) {
  const { data } = useApp();
  const [filter, setFilter] = useState<'all' | MemoryGroup>('all');
  if (!data) return null;
  const memories = data.memories;
  const tabs = [{ id: 'all' as const, tab: '全部' }, ...memoryGroups.filter(group => group.id !== 'other' || memories.some(memory => memoryGroup(memory) === 'other'))];
  const groups = memoryGroups.filter(group => filter === 'all' || filter === group.id);
  const count = memories.filter(memory => filter === 'all' || memoryGroup(memory) === filter).length;
  return <DetailPage title="教练记住的你" onClose={onClose} trailing={<Text style={styles.count}>{memories.length} 条</Text>}
    footer={<View style={styles.footer}>
      <Text style={styles.footerHint}>进入对话，告诉教练哪些内容变了</Text>
      <SettingsAction title="告诉教练有变化" icon="chatbubble-ellipses-outline" onPress={() => openCoach('我想更新一项个人资料：')} />
    </View>}>
    <View style={styles.info}><Ionicons name="chatbubble-ellipses-outline" size={22} color={palette.ink} /><Text style={styles.infoText}>来自你告诉教练的信息，有变化随时更新。</Text></View>
    <View style={styles.tabs} accessibilityRole="tablist">
      {tabs.map(tab => <Pressable key={tab.id} accessibilityRole="tab" accessibilityLabel={tab.tab} accessibilityState={{ selected: filter === tab.id }}
        onPress={() => setFilter(tab.id)} style={[styles.tab, filter === tab.id && styles.selected]}><Text style={[styles.tabText, filter === tab.id && styles.selectedText]}>{tab.tab}</Text></Pressable>)}
    </View>
    {!count && <View style={styles.empty}><Text style={styles.emptyTitle}>{memories.length ? '这一类还没有记忆' : '还没有长期记忆'}</Text>
      <Text style={settingsStyles.copy}>在对话里告诉教练你的目标、习惯和限制，{ '\n' }有充分依据的信息会被保存下来。</Text></View>}
    {groups.map(group => {
      const items = memories.filter(memory => memoryGroup(memory) === group.id);
      return items.length ? <View key={group.id} style={styles.group}>
        <Text accessibilityRole="header" style={styles.sectionTitle}>{group.title}</Text>
        <View style={styles.card}>{items.map((memory, index) => <MemoryRow key={memory.id} memory={memory} divider={index > 0} />)}</View>
      </View> : null;
    })}
    {!!memories.length && <Text style={styles.disclaimer}>以上内容用于个性化交流，不是诊断结论。</Text>}
  </DetailPage>;
}

function MemoryRow({ memory, divider }: { memory: MemoryItem; divider: boolean }) {
  const display = memoryPresentation(memory);
  return <View style={[styles.row, divider && styles.divider]}>
    <View style={[styles.icon, { backgroundColor: palette[display.tone] }]}><Ionicons name={display.icon} size={25} color={display.tone === 'gold' || display.tone === 'peach' ? palette.orange : palette.ink} /></View>
    <View style={styles.copy}>
      <View style={styles.labelRow}><Text style={styles.label}>{display.label}</Text>{memory.stale && <Text style={styles.badge}>● 待确认</Text>}</View>
      <Text style={[styles.value, display.emphasized && styles.emphasized]}>{display.value}</Text>
      {display.detail && <Text style={styles.detail}>{display.detail}</Text>}
      {memory.stale && <Text style={styles.detail}>这项信息可能有变化，需要再确认。</Text>}
    </View>
  </View>;
}

const styles = StyleSheet.create({
  count: { color: palette.muted, fontSize: 12 }, info: { flexDirection: 'row', gap: 10, alignItems: 'center', padding: 12, borderRadius: 14, backgroundColor: palette.lavender },
  infoText: { color: '#616A8B', fontSize: 12, lineHeight: 20, flex: 1 }, tabs: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 4 },
  tab: { flex: 1, minWidth: 58, minHeight: 38, justifyContent: 'center', alignItems: 'center', paddingHorizontal: 10, borderWidth: 1, borderColor: palette.line, backgroundColor: palette.surface, borderRadius: 12 },
  selected: { backgroundColor: palette.lavender, borderColor: palette.lavender }, tabText: { color: palette.muted, fontSize: 13 }, selectedText: { color: palette.ink, fontWeight: '700' },
  group: { gap: 8 }, sectionTitle: { color: palette.ink, fontSize: 19, lineHeight: 27, fontWeight: '700' },
  card: { ...palette.shadow, backgroundColor: palette.surface, borderRadius: 18, paddingHorizontal: 12 }, row: { paddingVertical: 14, flexDirection: 'row', gap: 14, alignItems: 'flex-start' },
  divider: { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: palette.line }, icon: { width: 46, height: 46, borderRadius: 14, alignItems: 'center', justifyContent: 'center' },
  copy: { flex: 1, gap: 5 }, labelRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  label: { color: '#616A8B', fontSize: 12, lineHeight: 18, flexShrink: 1 }, badge: { color: palette.orange, backgroundColor: '#FFF0E6', fontSize: 10, lineHeight: 17, paddingVertical: 3, paddingHorizontal: 8, borderRadius: 15 },
  value: { color: palette.ink, fontSize: 15, lineHeight: 23, fontWeight: '600' }, emphasized: { fontSize: 32, lineHeight: 40, fontWeight: '700' },
  detail: { color: '#616A8B', fontSize: 11, lineHeight: 18 }, disclaimer: { color: palette.muted, fontSize: 11, lineHeight: 18, textAlign: 'center', marginTop: 4 },
  footer: { gap: 8 }, footerHint: { color: '#616A8B', fontSize: 11, lineHeight: 18, paddingHorizontal: 4 },
  empty: { paddingVertical: 30, alignItems: 'center', gap: 12 }, emptyTitle: { color: palette.ink, fontSize: 17, fontWeight: '600' },
});
