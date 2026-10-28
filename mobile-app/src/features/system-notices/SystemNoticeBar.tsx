import { Ionicons } from '@expo/vector-icons';
import React, { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { colors, radius, spacing } from '../../theme';
import type { SystemNotice } from './noticeFeed';

export function SystemNoticeBar({ notices, onDismiss, onReply }: {
  notices: SystemNotice[]; onDismiss: (id: string) => void; onReply: () => void;
}) {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  if (!notices.length) return null;
  // Questions remain reachable while transient operation feedback arrives.
  const notice = notices.find(item => item.requiresResponse) || notices[0];
  const expanded = notice.requiresResponse || expandedId === notice.id;
  return <View style={styles.container} accessibilityLiveRegion="polite">
    <View style={styles.header}>
      <Ionicons name={notice.requiresResponse ? 'help-circle-outline' : 'checkmark-circle-outline'} size={18} color={colors.primary} />
      <Text style={styles.label}>{notice.requiresResponse ? '需要补充' : '系统提示'}{notices.length > 1 ? ` · ${notices.length} 条` : ''}</Text>
      <Pressable accessibilityRole="button" accessibilityLabel="关闭系统提示" onPress={() => onDismiss(notice.id)} hitSlop={10}><Ionicons name="close" size={19} color={colors.inkMuted} /></Pressable>
    </View>
    <ScrollView style={styles.content} nestedScrollEnabled>
      <Text style={styles.text} numberOfLines={expanded ? undefined : 3}>{notice.text}</Text>
    </ScrollView>
    <View style={styles.footer}>
      {notice.requiresResponse ? <Pressable accessibilityRole="button" onPress={onReply}><Text style={styles.action}>回复这条提示</Text></Pressable>
        : <Pressable accessibilityRole="button" onPress={() => setExpandedId(expanded ? null : notice.id)}><Text style={styles.action}>{expanded ? '收起' : '展开'}</Text></Pressable>}
    </View>
  </View>;
}

const styles = StyleSheet.create({
  container: { marginHorizontal: spacing.lg, marginBottom: spacing.sm, padding: spacing.md, borderRadius: radius.md, backgroundColor: colors.primarySoft, borderWidth: StyleSheet.hairlineWidth, borderColor: colors.line },
  header: { flexDirection: 'row', alignItems: 'center', gap: 7, marginBottom: 6 }, label: { flex: 1, color: colors.primary, fontSize: 12, fontWeight: '700' },
  content: { maxHeight: 160 }, text: { color: colors.ink, fontSize: 14, lineHeight: 21 },
  footer: { marginTop: 7, alignItems: 'flex-end' }, action: { color: colors.primary, fontWeight: '700', fontSize: 12 },
});
