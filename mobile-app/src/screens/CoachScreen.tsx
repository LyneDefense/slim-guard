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
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { BrandMark } from '../components/ui';
import { useApp } from '../context/AppContext';
import { colors, radius, spacing } from '../theme';
import type { ChatMessage } from '../types';

type SelectedImage = { uri: string; mimeType: 'image/jpeg' };

export function CoachScreen({ draft, consumeDraft }: { draft: string; consumeDraft: () => void }) {
  const { data, sendMessage, online, pending } = useApp();
  const [text, setText] = useState('');
  const [image, setImage] = useState<SelectedImage | null>(null);
  const [sending, setSending] = useState(false);
  const scrollRef = useRef<ScrollView>(null);

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
    if (source === 'camera') {
      const permission = await ImagePicker.requestCameraPermissionsAsync();
      if (!permission.granted) {
        Alert.alert('需要相机权限', '开启相机权限后，才能直接拍下饮食。');
        return;
      }
    }
    const result = source === 'camera'
      ? await ImagePicker.launchCameraAsync({ mediaTypes: ['images'], quality: 0.8 })
      : await ImagePicker.launchImageLibraryAsync({ mediaTypes: ['images'], quality: 0.8 });
    if (result.canceled) return;
    const asset = result.assets[0];
    const context = ImageManipulator.manipulate(asset.uri);
    if (asset.width > 1600) context.resize({ width: 1600, height: null });
    const rendered = await context.renderAsync();
    const converted = await rendered.saveAsync({ compress: 0.72, format: SaveFormat.JPEG });
    setImage({ uri: converted.uri, mimeType: 'image/jpeg' });
  }

  function showImageMenu() {
    Alert.alert('添加饮食照片', '我会把照片和你的文字一起交给教练理解。', [
      { text: '拍照', onPress: () => void chooseImage('camera') },
      { text: '从相册选择', onPress: () => void chooseImage('library') },
      { text: '取消', style: 'cancel' },
    ]);
  }

  async function send() {
    if ((!text.trim() && !image) || sending) return;
    const messageText = text;
    const messageImage = image;
    setText('');
    setImage(null);
    setSending(true);
    try {
      const result = await sendMessage(messageText, messageImage || undefined);
      if (result === 'queued') Alert.alert('已放进待发送', '网络恢复后会自动发送，不需要再发一次。');
    } catch {
      setText(messageText);
      setImage(messageImage);
    } finally {
      setSending(false);
    }
  }

  const messages = data?.messages || [];
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
              <Ionicons name="cloud-offline-outline" size={17} color={colors.warning} />
              <Text style={styles.queueText}>{pending.length} 条消息将在联网后自动发送</Text>
            </View>
          ) : null}
        </ScrollView>
        {image ? (
          <View style={styles.previewBar}>
            <Image source={{ uri: image.uri }} style={styles.previewImage} />
            <Text style={styles.previewText}>照片已准备好</Text>
            <Pressable onPress={() => setImage(null)} style={styles.removeImage}><Ionicons name="close" size={19} color={colors.ink} /></Pressable>
          </View>
        ) : null}
        <View style={styles.composer}>
          <Pressable accessibilityLabel="添加照片" onPress={showImageMenu} style={styles.addButton}>
            <Ionicons name="camera-outline" size={24} color={colors.primary} />
          </Pressable>
          <TextInput
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
            disabled={(!text.trim() && !image) || sending}
            onPress={() => void send()}
            style={[styles.sendButton, ((!text.trim() && !image) || sending) && styles.sendDisabled]}
          >
            <Ionicons name={sending ? 'hourglass-outline' : 'arrow-up'} size={21} color={colors.white} />
          </Pressable>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function Bubble({ message }: { message: ChatMessage }) {
  const mine = message.role === 'user';
  const displayedText = (message.text || (message.kind === 'image' ? '📷 图片' : '')).replace(/\*\*/g, '');
  return (
    <View style={[styles.bubbleRow, mine && styles.bubbleRowMine]}>
      {!mine ? <View style={styles.smallAvatar}><Ionicons name="leaf" size={13} color={colors.white} /></View> : null}
      <View style={[styles.bubble, mine ? styles.bubbleMine : styles.bubbleCoach, message.failed && styles.bubbleFailed]}>
        <Text style={[styles.bubbleText, mine && styles.bubbleTextMine]}>{displayedText}</Text>
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
  bubbleFailed: { borderWidth: 1, borderColor: colors.danger },
  bubbleText: { color: colors.ink, fontSize: 15, lineHeight: 23 },
  bubbleTextMine: { color: colors.white },
  messageState: { color: colors.inkMuted, fontSize: 10, marginTop: 4, textAlign: 'right' },
  failedText: { color: colors.danger, fontSize: 10, marginTop: 4 },
  queueBanner: { alignSelf: 'center', flexDirection: 'row', alignItems: 'center', gap: 7, backgroundColor: colors.accentSoft, borderRadius: radius.pill, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, marginTop: spacing.sm },
  queueText: { color: colors.warning, fontSize: 12, fontWeight: '600' },
  previewBar: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: spacing.lg, paddingVertical: spacing.sm, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.line, backgroundColor: colors.surface },
  previewImage: { width: 48, height: 48, borderRadius: 12 },
  previewText: { color: colors.ink, fontSize: 13, fontWeight: '600', marginLeft: spacing.md },
  removeImage: { marginLeft: 'auto', width: 34, height: 34, borderRadius: 17, backgroundColor: colors.surfaceMuted, alignItems: 'center', justifyContent: 'center' },
  composer: { flexDirection: 'row', alignItems: 'flex-end', paddingHorizontal: spacing.md, paddingVertical: 10, gap: spacing.sm, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.line, backgroundColor: colors.surface },
  addButton: { width: 44, height: 44, borderRadius: 16, backgroundColor: colors.primarySoft, alignItems: 'center', justifyContent: 'center' },
  input: { flex: 1, minHeight: 44, maxHeight: 116, borderRadius: 18, backgroundColor: colors.surfaceMuted, color: colors.ink, fontSize: 15, lineHeight: 21, paddingHorizontal: spacing.lg, paddingVertical: 11 },
  sendButton: { width: 44, height: 44, borderRadius: 16, backgroundColor: colors.primary, alignItems: 'center', justifyContent: 'center' },
  sendDisabled: { opacity: 0.35 },
});
