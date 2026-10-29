import { Ionicons } from '@expo/vector-icons';
import React, { useEffect, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { appearance as palette } from '../../shared/ui/appearance';
import type { SystemNotice } from './noticeFeed';

export function NoticeCountdown({ expiresAt }: { expiresAt: number }) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [expiresAt]);
  const seconds = Math.max(0, Math.ceil((expiresAt - now) / 1000));
  // Expiration remains owned by useSystemNotices; this is only a visual clock.
  return <Text accessibilityLabel={`提示将在 ${seconds} 秒后消失`} style={styles.countdown}>{seconds}s</Text>;
}

export function SystemNoticeBar({ notices, onDismiss, onReply }: {
  notices: SystemNotice[]; onDismiss: (id: string) => void; onReply: () => void;
}) {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  if (!notices.length) return null;
  const notice = notices.find(item => item.requiresResponse) || notices[0];
  const expanded = notice.requiresResponse || expandedId === notice.id;
  const icon = notice.requiresResponse ? 'help' : notice.outcome === 'success' ? 'checkmark' : notice.outcome === 'failed' ? 'alert' : 'information';
  return <View style={[styles.container, notice.outcome === 'failed' && styles.failure]} accessibilityLiveRegion="polite">
    <View style={styles.row}>
      <View style={styles.icon}><Ionicons name={icon} size={15} color={palette.surface} /></View>
      <Pressable accessibilityRole="button" accessibilityLabel={expanded ? '收起系统提示详情' : '查看系统提示详情'}
        disabled={notice.requiresResponse} onPress={() => setExpandedId(expanded ? null : notice.id)} style={styles.summary}>
        <Text style={styles.summaryText} numberOfLines={expanded ? 1 : 2}>{notice.requiresResponse ? '需要补充' : notice.text}</Text>
        {notices.length > 1 && <Text style={styles.count}>还有 {notices.length - 1} 条提示</Text>}
      </Pressable>
      {!notice.requiresResponse && notice.expiresAt !== null && <NoticeCountdown expiresAt={notice.expiresAt} />}
      <Pressable accessibilityRole="button" accessibilityLabel="关闭系统提示" onPress={() => onDismiss(notice.id)} style={styles.close}>
        <Ionicons name="close" size={18} color="#827A76" />
      </Pressable>
    </View>
    {expanded && <>
      <ScrollView style={styles.details} nestedScrollEnabled><Text selectable style={styles.text}>{notice.text}</Text></ScrollView>
      <Pressable accessibilityRole="button" onPress={notice.requiresResponse ? onReply : () => setExpandedId(null)} style={styles.action}>
        <Text style={styles.actionText}>{notice.requiresResponse ? '回复这条提示' : '收起'}</Text>
      </Pressable>
    </>}
  </View>;
}

const styles = StyleSheet.create({
  container: { marginHorizontal: 16, marginBottom: 5, borderRadius: 13, backgroundColor: '#FFECDC', paddingLeft: 11, paddingRight: 2 },
  failure: { backgroundColor: '#FBE5DE' },
  row: { flexDirection: 'row', alignItems: 'center', gap: 9, minHeight: 36 },
  icon: { width: 20, height: 20, borderRadius: 10, backgroundColor: '#E76C20', alignItems: 'center', justifyContent: 'center' },
  summary: { flex: 1, paddingVertical: 8, minWidth: 0, minHeight: 36, justifyContent: 'center' },
  summaryText: { color: '#6D371F', fontSize: 12, lineHeight: 18, fontWeight: '600' },
  count: { color: '#876B5B', fontSize: 10, marginTop: 2 },
  countdown: { color: '#857B76', fontSize: 11 }, close: { width: 44, height: 44, marginVertical: -4, alignItems: 'center', justifyContent: 'center' },
  details: { maxHeight: 144, marginRight: 12, marginTop: 2 }, text: { color: palette.ink, fontSize: 14, lineHeight: 21 },
  action: { minHeight: 44, justifyContent: 'center', alignSelf: 'flex-end', paddingHorizontal: 14 },
  actionText: { color: palette.orangeInk, fontSize: 12, fontWeight: '600' },
});
