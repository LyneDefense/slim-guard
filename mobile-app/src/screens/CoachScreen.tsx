import { Ionicons } from '@expo/vector-icons';
import { ImageManipulator, SaveFormat } from 'expo-image-manipulator';
import * as ImagePicker from 'expo-image-picker';
import React, { useEffect, useRef, useState } from 'react';
import {
  Alert,
  Image,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
  type ScrollViewInstance,
  type TextInputInstance,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { BrandMark } from '../components/ui';
import { PhotoGallery } from '../components/PhotoGallery';
import { useApp } from '../context/AppContext';
import { SystemNoticeBar } from '../features/system-notices/SystemNoticeBar';
import { colors, radius, spacing } from '../theme';
import type { ChatMessage } from '../types';

type SelectedImage = { uri: string; mimeType: 'image/jpeg' };

export function CoachScreen({ draft, consumeDraft }: { draft: string; consumeDraft: () => void }) {
  const { data, sendMessage, online, pending, notices, dismissNotice } = useApp();
  const [text, setText] = useState('');
  const [images, setImages] = useState<SelectedImage[]>([]);
  const [sending, setSending] = useState(false);
  const scrollRef = useRef<ScrollViewInstance>(null);
  const inputRef = useRef<TextInputInstance>(null);

  useEffect(() => {
    if (!draft) return;
    setText(draft);
    consumeDraft();
  }, [draft, consumeDraft]);

  useEffect(() => {
    const timeout = setTimeout(() => scrollRef.current?.scrollToEnd({ animated: true }), 80);
    return () => clearTimeout(timeout);
  }, [data?.messages.length, pending.length]);

  async function chooseImage(source: 'camera' | 'library') {
    if (images.length >= 8) {
      Alert.alert('照片数量已达上限', '一条消息最多可以发送 8 张照片。');
      return;
    }
    if (source === 'camera') {
      const permission = await ImagePicker.requestCameraPermissionsAsync();
      if (!permission.granted) {
        Alert.alert('需要相机权限', '开启相机权限后，才能直接拍下饮食。');
        return;
      }
    }
    const result = source === 'camera'
      ? await ImagePicker.launchCameraAsync({ mediaTypes: ['images'], quality: 0.8 })
      : await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ['images'],
        quality: 0.8,
        allowsMultipleSelection: true,
        selectionLimit: Math.max(1, 8 - images.length),
      });
    if (result.canceled) return;
    const selected = result.assets.slice(0, Math.max(0, 8 - images.length));
    const converted = await Promise.all(selected.map(async (asset) => {
      const context = ImageManipulator.manipulate(asset.uri);
      if (asset.width > 1600) context.resize({ width: 1600, height: null });
      const rendered = await context.renderAsync();
      const saved = await rendered.saveAsync({ compress: 0.72, format: SaveFormat.JPEG });
      return { uri: saved.uri, mimeType: 'image/jpeg' as const };
    }));
    setImages((current) => [...current, ...converted]);
  }

  function showImageMenu() {
    Alert.alert('添加饮食照片', '我会把照片和你的文字一起交给教练理解。', [
      { text: '拍照', onPress: () => void chooseImage('camera') },
      { text: '从相册选择', onPress: () => void chooseImage('library') },
      { text: '取消', style: 'cancel' },
    ]);
  }

  async function send() {
    if ((!text.trim() && !images.length) || sending) return;
    const messageText = text;
    const messageImages = images;
    setText('');
    setImages([]);
    setSending(true);
    try {
      const result = await sendMessage(messageText, messageImages);
      if (result === 'queued') Alert.alert('已放进待发送', '网络恢复后会自动发送，不需要再发一次。');
    } catch {
      setText(messageText);
      setImages(messageImages);
    } finally {
      setSending(false);
    }
  }

  const messages = (data?.messages || []).filter(message => message.participant !== 'system_assistant');
  return (
    <SafeAreaView style={styles.safe} edges={['top']}>
      <KeyboardAvoidingView style={styles.flex} behavior={Platform.OS === 'ios' ? 'padding' : undefined} keyboardVerticalOffset={4}>
        <View style={styles.header}>
          <BrandMark compact />
          <View style={styles.coachState}>
            <View style={[styles.dot, { backgroundColor: online ? colors.primary : colors.warning }]} />
            <Text style={styles.coachStateText}>{online ? '教练在线' : '离线模式'}</Text>
          </View>
        </View>
        <ScrollView ref={scrollRef} contentContainerStyle={styles.messages} keyboardShouldPersistTaps="handled">
          <View style={styles.intro}>
            <View style={styles.avatar}><Ionicons name="leaf" size={20} color={colors.white} /></View>
            <Text style={styles.introTitle}>今天想从哪里开始？</Text>
            <Text style={styles.introText}>说人话就行。记数据、发吃的、问问题，或者只是聊聊都可以。</Text>
          </View>
          {messages.map((message) => <Bubble key={message.id} message={message} />)}
          {pending.length > 0 ? (
            <View style={styles.queueBanner}>
              <Ionicons name={online ? 'sync-outline' : 'cloud-offline-outline'} size={17} color={colors.warning} />
              <Text style={styles.queueText}>
                {online ? `${pending.length} 条消息正在等待服务器确认` : `${pending.length} 条消息将在联网后自动发送`}
              </Text>
            </View>
          ) : null}
        </ScrollView>
        <SystemNoticeBar notices={notices} onDismiss={dismissNotice} onReply={() => inputRef.current?.focus()} />
        {images.length ? (
          <View style={styles.previewBar}>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.previewList}>
              {images.map((image, index) => (
                <View key={`${image.uri}-${index}`} style={styles.previewItem}>
                  <Image source={{ uri: image.uri }} style={styles.previewImage} />
                  <Pressable onPress={() => setImages((current) => current.filter((_, itemIndex) => itemIndex !== index))} style={styles.removeImage} accessibilityLabel="移除照片">
                    <Ionicons name="close" size={14} color={colors.ink} />
                  </Pressable>
                </View>
              ))}
              <Text style={styles.previewText}>{images.length} 张照片将和文字一起发送</Text>
            </ScrollView>
          </View>
        ) : null}
        <View style={styles.composer}>
          <Pressable accessibilityLabel="添加照片" onPress={showImageMenu} style={styles.addButton}>
            <Ionicons name="camera-outline" size={24} color={colors.primary} />
          </Pressable>
          <TextInput
            ref={inputRef}
            accessibilityLabel="发给减脂教练的消息"
            multiline
            maxLength={20_000}
            value={text}
            onChangeText={setText}
            placeholder="告诉教练你刚刚吃了、量了或想到什么…"
            placeholderTextColor="#909892"
            style={styles.input}
          />
          <Pressable
            accessibilityLabel="发送"
            disabled={(!text.trim() && !images.length) || sending}
            onPress={() => void send()}
            style={[styles.sendButton, ((!text.trim() && !images.length) || sending) && styles.sendDisabled]}
          >
            <Ionicons name={sending ? 'hourglass-outline' : 'arrow-up'} size={21} color={colors.white} />
          </Pressable>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function Bubble({ message }: { message: ChatMessage }) {
  const mine = message.participant === 'user' || message.role === 'user';
  const displayedText = (message.text || (message.kind === 'image' && !message.attachments?.length ? '📷 图片' : '')).replace(/\*\*/g, '');
  return (
    <View style={[styles.bubbleRow, mine && styles.bubbleRowMine]}>
      {!mine ? <View style={styles.smallAvatar}><Ionicons name="medkit-outline" size={13} color={colors.white} /></View> : null}
      <View style={[styles.bubble, mine ? styles.bubbleMine : styles.bubbleCoach, message.failed && styles.bubbleFailed]}>
        {!mine ? <Text style={styles.senderLabel}>医生教练</Text> : null}
        {message.attachments?.length ? <PhotoGallery photos={message.attachments} /> : null}
        {message.kind !== 'card' && displayedText ? <Text style={[styles.bubbleText, mine && styles.bubbleTextMine]}>{displayedText}</Text> : null}
        {message.pending ? <Text style={[styles.messageState, mine && { color: '#D8E5DC' }]}>发送中…</Text> : null}
        {message.failed ? <Text style={styles.failedText}>发送失败，可重新发送</Text> : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  safe: { flex: 1, backgroundColor: colors.background },
  header: { height: 64, paddingHorizontal: spacing.lg, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.line },
  coachState: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  dot: { width: 7, height: 7, borderRadius: 4 },
  coachStateText: { color: colors.inkMuted, fontSize: 12, fontWeight: '600' },
  messages: { padding: spacing.lg, paddingBottom: spacing.xl, flexGrow: 1 },
  intro: { alignItems: 'center', paddingVertical: spacing.xl, paddingHorizontal: spacing.xl },
  avatar: { width: 48, height: 48, borderRadius: 18, alignItems: 'center', justifyContent: 'center', backgroundColor: colors.primary, marginBottom: spacing.md },
  introTitle: { color: colors.ink, fontSize: 20, fontWeight: '800' },
  introText: { color: colors.inkMuted, fontSize: 13, lineHeight: 20, textAlign: 'center', marginTop: 5 },
  bubbleRow: { flexDirection: 'row', alignItems: 'flex-end', marginBottom: spacing.md, maxWidth: '90%' },
  bubbleRowMine: { alignSelf: 'flex-end', justifyContent: 'flex-end' },
  smallAvatar: { width: 28, height: 28, borderRadius: 10, backgroundColor: colors.primary, alignItems: 'center', justifyContent: 'center', marginRight: spacing.sm },
  bubble: { borderRadius: 19, paddingHorizontal: spacing.lg, paddingVertical: spacing.md },
  bubbleMine: { backgroundColor: colors.primary, borderBottomRightRadius: 6 },
  bubbleCoach: { backgroundColor: colors.surface, borderBottomLeftRadius: 6, borderWidth: StyleSheet.hairlineWidth, borderColor: colors.line },
  senderLabel: { color: colors.inkMuted, fontSize: 11, fontWeight: '700', marginBottom: 4 },
  bubbleFailed: { borderWidth: 1, borderColor: colors.danger },
  bubbleText: { color: colors.ink, fontSize: 15, lineHeight: 23 },
  bubbleTextMine: { color: colors.white },
  messageState: { color: colors.inkMuted, fontSize: 10, marginTop: 4, textAlign: 'right' },
  failedText: { color: colors.danger, fontSize: 10, marginTop: 4 },
  queueBanner: { alignSelf: 'center', flexDirection: 'row', alignItems: 'center', gap: 7, backgroundColor: colors.accentSoft, borderRadius: radius.pill, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, marginTop: spacing.sm },
  queueText: { color: colors.warning, fontSize: 12, fontWeight: '600' },
  previewBar: { paddingHorizontal: spacing.lg, paddingVertical: spacing.sm, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.line, backgroundColor: colors.surface },
  previewList: { alignItems: 'center', gap: spacing.sm },
  previewItem: { position: 'relative' },
  previewImage: { width: 54, height: 54, borderRadius: 12 },
  previewText: { color: colors.inkMuted, fontSize: 12, fontWeight: '600', marginLeft: spacing.xs },
  removeImage: { position: 'absolute', right: -5, top: -5, width: 22, height: 22, borderRadius: 11, backgroundColor: colors.surface, alignItems: 'center', justifyContent: 'center', elevation: 1 },
  composer: { flexDirection: 'row', alignItems: 'flex-end', paddingHorizontal: spacing.md, paddingVertical: 10, gap: spacing.sm, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.line, backgroundColor: colors.surface },
  addButton: { width: 44, height: 44, borderRadius: 16, backgroundColor: colors.primarySoft, alignItems: 'center', justifyContent: 'center' },
  input: { flex: 1, minHeight: 44, maxHeight: 116, borderRadius: 18, backgroundColor: colors.surfaceMuted, color: colors.ink, fontSize: 15, lineHeight: 21, paddingHorizontal: spacing.lg, paddingVertical: 11 },
  sendButton: { width: 44, height: 44, borderRadius: 16, backgroundColor: colors.primary, alignItems: 'center', justifyContent: 'center' },
  sendDisabled: { opacity: 0.35 },
});
