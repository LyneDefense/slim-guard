import React, { useCallback, useState } from 'react';
import { Image, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { mobileApi } from '../lib/api';
import type { ChatAttachment } from '../types';
import { colors, spacing } from '../theme';
import { PhotoViewer } from './PhotoViewer';
import { AppIcon } from '../shared/ui/AppIcon';

export function PhotoGallery({ photos, variant = 'gallery' }: {
  photos: ChatAttachment[]; variant?: 'gallery' | 'record' | 'chat';
}) {
  const [selected, setSelected] = useState<number | null>(null);
  const [unavailable, setUnavailable] = useState<Record<number, boolean>>({});
  const closeViewer = useCallback(() => setSelected(null), []);
  if (!photos.length) return null;
  const record = variant === 'record';
  const chat = variant === 'chat';
  const singlePreview = record || chat;
  const thumbnails = (singlePreview ? photos.slice(0, 1) : photos).map((photo, index) => {
    const source = mobileApi.imageSource(photo);
    if (!source) return null;
    const size = record ? styles.record : chat ? styles.chat : styles.large;
    return <Pressable key={photo.asset_id || photo.uri || index}
      accessibilityRole="button" accessibilityLabel={`查看第 ${index + 1} 张餐食照片${singlePreview && photos.length > 1 ? `，共 ${photos.length} 张` : ''}`}
      style={chat ? styles.chatFrame : undefined}
      onPress={() => setSelected(index)}>
      {unavailable[index] ? <View style={[styles.missing, size]}><Text style={styles.missingText}>照片暂不可用</Text></View>
        : <Image source={source} style={[styles.photo, size]} resizeMode="cover" onError={() => setUnavailable(previous => ({ ...previous, [index]: true }))} />}
      {record && <View pointerEvents="none" style={styles.expand}><AppIcon name="expand" size={17} color="white" /></View>}
      {singlePreview && photos.length > 1 && <View pointerEvents="none" style={styles.count}><Text style={styles.countText}>{photos.length} 张</Text></View>}
    </Pressable>;
  });
  return <>
    {singlePreview ? <View style={record ? styles.record : styles.chatFrame}>{thumbnails}</View> :
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.thumbnails}>
      {thumbnails}
    </ScrollView>}
    {selected !== null ? <PhotoViewer photos={photos} initialIndex={selected} onClose={closeViewer} /> : null}
  </>;
}

const styles = StyleSheet.create({
  thumbnails: { gap: spacing.sm, paddingVertical: spacing.sm },
  photo: { borderRadius: 12, backgroundColor: colors.surfaceMuted },
  large: { width: 220, height: 220 },
  record: { width: 110, height: 106, flexShrink: 0 },
  chatFrame: { width: '100%' }, chat: { width: '100%', aspectRatio: 1.55 },
  expand: { position: 'absolute', bottom: 4, right: 4, width: 27, height: 27, borderRadius: 14, backgroundColor: '#282B3299', alignItems: 'center', justifyContent: 'center' },
  count: { position: 'absolute', top: 5, right: 5, borderRadius: 8, backgroundColor: '#282B3299', paddingHorizontal: 5, paddingVertical: 2 },
  countText: { color: 'white', fontSize: 10 },
  missingText: { color: colors.inkMuted, fontSize: 11 },
  missing: { alignItems: 'center', justifyContent: 'center', backgroundColor: colors.surfaceMuted, borderRadius: 12 },
});
