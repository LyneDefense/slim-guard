import React, { useCallback, useState } from 'react';
import { Image, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { mobileApi } from '../lib/api';
import type { ChatAttachment } from '../types';
import { colors, spacing } from '../theme';
import { PhotoViewer } from './PhotoViewer';

export function PhotoGallery({ photos, compact = false }: { photos: ChatAttachment[]; compact?: boolean }) {
  const [selected, setSelected] = useState<number | null>(null);
  const [unavailable, setUnavailable] = useState<Record<number, boolean>>({});
  const closeViewer = useCallback(() => setSelected(null), []);
  if (!photos.length) return null;
  return <>
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.thumbnails}>
      {photos.map((photo, index) => {
        const source = mobileApi.imageSource(photo);
        return source ? <Pressable
          key={photo.asset_id || photo.uri || index}
          accessibilityRole="button" accessibilityLabel={`查看第 ${index + 1} 张餐食照片`}
          onPress={() => setSelected(index)}
        >
          {unavailable[index] ? <View style={[styles.missing, compact ? styles.compact : styles.large]}><Text>照片暂不可用</Text></View>
            : <Image source={source} style={[styles.photo, compact ? styles.compact : styles.large]} resizeMode="cover" onError={() => setUnavailable(previous => ({ ...previous, [index]: true }))} />}
        </Pressable> : null;
      })}
    </ScrollView>
    {selected !== null ? <PhotoViewer photos={photos} initialIndex={selected} onClose={closeViewer} /> : null}
  </>;
}

const styles = StyleSheet.create({
  thumbnails: { gap: spacing.sm, paddingVertical: spacing.sm },
  photo: { borderRadius: 12, backgroundColor: colors.surfaceMuted },
  compact: { width: 76, height: 76 }, large: { width: 220, height: 220 },
  missing: { alignItems: 'center', justifyContent: 'center', backgroundColor: colors.surfaceMuted, borderRadius: 12 },
});
