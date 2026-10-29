import React from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { PhotoGallery } from '../../components/PhotoGallery';
import { appearance as palette } from '../../shared/ui/appearance';
import type { ChatMessage } from '../../types';
import { CoachAvatar, coachIdentity } from './CoachIdentity';

export function ChatBubble({ message }: { message: ChatMessage }) {
  if (message.participant === 'system_assistant' || message.kind === 'card') return null;
  const mine = message.participant === 'user' || message.role === 'user';
  const media = !!message.attachments?.length;
  const displayedText = (message.text || (message.kind === 'image' && !media ? '📷 图片' : '')).replace(/\*\*/g, '');
  return <View style={[styles.row, mine && styles.mine]}>
    {!mine && <View style={styles.avatar}><CoachAvatar /></View>}
    <View style={styles.column}>
      {!mine && <Text style={styles.sender}>{coachIdentity.name}</Text>}
      <View style={[styles.bubble, mine ? styles.userBubble : styles.coachBubble, media && styles.media, message.failed && styles.failed]}>
        {media && <PhotoGallery variant="chat" photos={message.attachments!} />}
        {!!displayedText && <Text selectable style={[styles.text, mine && styles.userText, media && styles.caption]}>{displayedText}</Text>}
        {message.pending && <Text style={[styles.state, mine && styles.userState]}>{message.delivery_state === 'queued' ? '等待发送…' : message.delivery_state === 'processing' ? '正在处理…' : '发送中…'}</Text>}
        {message.failed && <Text style={[styles.state, mine ? styles.userState : styles.failureText]}>发送失败，可重新发送</Text>}
      </View>
    </View>
  </View>;
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'flex-start', gap: 10, marginBottom: 12, maxWidth: '78%' },
  mine: { alignSelf: 'flex-end', maxWidth: '84%' },
  avatar: { paddingTop: 4 }, column: { flexShrink: 1, minWidth: 0 },
  sender: { color: '#81889D', fontSize: 11, lineHeight: 17, marginBottom: 5 },
  bubble: { borderRadius: 18, paddingHorizontal: 13, paddingVertical: 11 },
  userBubble: { backgroundColor: '#303E79' },
  coachBubble: { ...palette.shadow, backgroundColor: palette.surface },
  media: { width: 220, maxWidth: '100%', padding: 12 },
  text: { color: palette.ink, fontSize: 15, lineHeight: 22 },
  userText: { color: palette.surface }, caption: { marginTop: 9 },
  state: { color: palette.muted, fontSize: 10, lineHeight: 16, marginTop: 5, textAlign: 'right' },
  userState: { color: '#E0E4FA' }, failed: { borderWidth: 1, borderColor: '#CA6D5B' }, failureText: { color: '#A33C32' },
});
