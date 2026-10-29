import { Ionicons } from '@expo/vector-icons';
import React, { useEffect, useRef } from 'react';
import { Keyboard, KeyboardAvoidingView, Platform, ScrollView, StyleSheet, Text, View } from 'react-native';
import type { ScrollViewInstance, TextInputInstance } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { useApp } from '../context/AppContext';
import { ChatBubble } from '../features/coach-chat/ChatBubble';
import { ChatComposer, SelectedPhotoStrip } from '../features/coach-chat/ChatComposer';
import { CoachIdentity } from '../features/coach-chat/CoachIdentity';
import { conversationItems } from '../features/coach-chat/conversation';
import { useCoachComposer } from '../features/coach-chat/useCoachComposer';
import { SystemNoticeBar } from '../features/system-notices/SystemNoticeBar';
import { BrandHeader } from '../shared/brand/BrandHeader';
import { appearance as palette } from '../shared/ui/appearance';
import { connectionLabel } from '../shared/network/connection';

export function CoachScreen({ draft, consumeDraft }: { draft: string; consumeDraft: () => void }) {
  const { data, sendMessage, connection, pending, notices, dismissNotice } = useApp();
  const disconnected = connection.device === 'disconnected';
  const composer = useCoachComposer(draft, consumeDraft, sendMessage);
  const scrollRef = useRef<ScrollViewInstance>(null);
  const stickToBottom = useRef(true);
  const inputRef = useRef<TextInputInstance>(null);
  const items = conversationItems(data?.messages || [], data?.routine.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone);
  useEffect(() => {
    const timer = setTimeout(() => scrollRef.current?.scrollToEnd({ animated: true }), 80);
    return () => clearTimeout(timer);
  }, [data?.messages.length, pending.length]);
  useEffect(() => {
    const listener = Keyboard.addListener('keyboardDidShow', () => scrollRef.current?.scrollToEnd({ animated: true }));
    return () => listener.remove();
  }, []);
  const settleScroll = () => {
    if (stickToBottom.current) scrollRef.current?.scrollToEnd({ animated: false });
  };

  return <SafeAreaView style={styles.safe} edges={['top']}>
    <BrandHeader status={connectionLabel(connection)} />
    <KeyboardAvoidingView style={styles.flex} behavior={Platform.OS === 'ios' ? 'padding' : undefined} keyboardVerticalOffset={4}>
      <CoachIdentity />
      <ScrollView ref={scrollRef} contentContainerStyle={styles.messages} keyboardShouldPersistTaps="handled"
        onLayout={settleScroll} onContentSizeChange={settleScroll} scrollEventThrottle={16}
        onScroll={({ nativeEvent: { contentOffset, contentSize, layoutMeasurement } }) => {
          stickToBottom.current = contentSize.height - contentOffset.y - layoutMeasurement.height < 72;
        }}>
        {!items.length && <Text style={styles.empty}>拍下这一餐，记下体重，{ '\n' }或者聊聊今天。慢慢来就好。</Text>}
        {items.map(({ message, timeLabel }) => <View key={message.id}>
          {timeLabel && <Text style={styles.time}>{timeLabel}</Text>}
          <ChatBubble message={message} />
        </View>)}
        {pending.length > 0 && <View style={styles.queue}>
          <Ionicons name={disconnected ? 'cloud-offline-outline' : 'sync-outline'} size={16} color={palette.orange} />
          <Text style={styles.queueText}>{disconnected ? `${pending.length} 条消息将在联网后自动发送` : `${pending.length} 条消息正在发送或等待处理，将自动重试`}</Text>
        </View>}
      </ScrollView>
      <SelectedPhotoStrip photos={composer.images} onRemove={composer.removePhoto} />
      <SystemNoticeBar notices={notices} onDismiss={dismissNotice} onReply={() => inputRef.current?.focus()} />
      <ChatComposer composer={composer} inputRef={inputRef} />
    </KeyboardAvoidingView>
  </SafeAreaView>;
}

const styles = StyleSheet.create({
  flex: { flex: 1 }, safe: { flex: 1, backgroundColor: palette.background },
  messages: { flexGrow: 1, paddingHorizontal: 16, paddingTop: 4, paddingBottom: 4 },
  time: { textAlign: 'center', color: '#81889D', fontSize: 12, lineHeight: 18, marginTop: 3, marginBottom: 12 },
  empty: { color: palette.muted, textAlign: 'center', fontSize: 14, lineHeight: 24, marginTop: 44 },
  queue: { alignSelf: 'center', flexDirection: 'row', alignItems: 'center', gap: 7, borderRadius: 18, backgroundColor: '#FFF0E6', paddingHorizontal: 12, paddingVertical: 8, marginBottom: 8 },
  queueText: { flexShrink: 1, color: palette.orangeInk, fontSize: 12, lineHeight: 18 },
});
