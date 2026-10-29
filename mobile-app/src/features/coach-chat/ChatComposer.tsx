import { Ionicons } from '@expo/vector-icons';
import React from 'react';
import type { RefObject } from 'react';
import { ActivityIndicator, Image, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import type { TextInputInstance } from 'react-native';

import { appearance as palette } from '../../shared/ui/appearance';
import type { SelectedPhoto, useCoachComposer } from './useCoachComposer';

export function SelectedPhotoStrip({ photos, onRemove }: { photos: SelectedPhoto[]; onRemove: (index: number) => void }) {
  if (!photos.length) return null;
  return <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.preview} contentContainerStyle={styles.previewContent}>
    {photos.map((photo, index) => <View key={`${photo.uri}-${index}`}>
      <Image source={{ uri: photo.uri }} style={styles.previewImage} />
      <Pressable accessibilityRole="button" accessibilityLabel={`移除第 ${index + 1} 张照片`} onPress={() => onRemove(index)} hitSlop={10} style={styles.remove}>
        <Ionicons name="close" size={14} color={palette.ink} />
      </Pressable>
    </View>)}
    <Text style={styles.previewText}>{photos.length} 张照片将和文字一起发送</Text>
  </ScrollView>;
}

export function ChatComposer({ composer, inputRef }: {
  composer: ReturnType<typeof useCoachComposer>; inputRef: RefObject<TextInputInstance | null>;
}) {
  const disabled = (!composer.text.trim() && !composer.images.length) || composer.sending || composer.picking;
  return <View style={styles.composer}>
    <Pressable accessibilityRole="button" accessibilityLabel="添加照片" accessibilityState={{ busy: composer.picking }}
      disabled={composer.sending || composer.picking} onPress={composer.addPhoto} style={styles.camera}>
      {composer.picking ? <ActivityIndicator color={palette.ink} /> : <Ionicons name="camera-outline" size={25} color={palette.ink} />}
    </Pressable>
    <TextInput ref={inputRef} accessibilityLabel="发给减脂教练的消息" multiline maxLength={20_000}
      value={composer.text} onChangeText={composer.setText} placeholder="吃了什么，或者想聊聊…" placeholderTextColor="#878FA6" style={styles.input} />
    <Pressable accessibilityRole="button" accessibilityLabel="发送" accessibilityState={{ busy: composer.sending }} disabled={disabled}
      onPress={() => void composer.send()} style={[styles.send, disabled && styles.disabled]}>
      <Ionicons name={composer.sending ? 'hourglass-outline' : 'arrow-up'} size={25} color={palette.surface} />
    </Pressable>
  </View>;
}

const styles = StyleSheet.create({
  composer: { flexDirection: 'row', alignItems: 'flex-end', paddingHorizontal: 16, paddingTop: 5, paddingBottom: 12, gap: 10, backgroundColor: palette.background },
  camera: { width: 44, height: 44, marginBottom: 3, borderRadius: 15, backgroundColor: palette.lavender, alignItems: 'center', justifyContent: 'center' },
  input: { ...palette.shadow, flex: 1, minHeight: 50, maxHeight: 116, borderRadius: 24, backgroundColor: palette.surface, color: palette.ink, fontSize: 14, lineHeight: 21, paddingHorizontal: 16, paddingVertical: 14 },
  send: { width: 44, height: 44, marginBottom: 3, borderRadius: 22, backgroundColor: '#303E79', alignItems: 'center', justifyContent: 'center' },
  disabled: { opacity: 0.4 },
  preview: { maxHeight: 92, flexGrow: 0, backgroundColor: palette.background },
  previewContent: { paddingHorizontal: 16, paddingVertical: 12, gap: 16, alignItems: 'center' },
  previewImage: { width: 56, height: 56, borderRadius: 12 },
  previewText: { color: palette.muted, fontSize: 12 },
  remove: { position: 'absolute', right: -5, top: -5, width: 24, height: 24, borderRadius: 12, backgroundColor: palette.surface, alignItems: 'center', justifyContent: 'center' },
});
