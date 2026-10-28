import { Ionicons } from '@expo/vector-icons';
import React, { useState } from 'react';
import { Image, Modal, Pressable, ScrollView, StyleSheet, Text, View, useWindowDimensions } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { mobileApi } from '../lib/api';
import type { ChatAttachment } from '../types';
import { colors, spacing } from '../theme';

export function PhotoGallery({ photos, compact = false }: { photos: ChatAttachment[]; compact?: boolean }) {
  const [selected, setSelected] = useState<number | null>(null);
  const [current, setCurrent] = useState(0);
  const [unavailable, setUnavailable] = useState<Record<number, boolean>>({});
  const { width, height } = useWindowDimensions();
  if (!photos.length) return null;
  return <>
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.thumbnails}>
      {photos.map((photo, index) => {
        const source = mobileApi.imageSource(photo);
        return source ? <Pressable
          key={photo.asset_id || photo.uri || index}
          accessibilityRole="button" accessibilityLabel={`查看第 ${index + 1} 张餐食照片`}
          onPress={() => { setSelected(index); setCurrent(index); }}
        >
          {unavailable[index] ? <View style={[styles.missing, compact ? styles.compact : styles.large]}><Text>照片暂不可用</Text></View>
            : <Image source={source} style={[styles.photo, compact ? styles.compact : styles.large]} resizeMode="cover" onError={() => setUnavailable(previous => ({ ...previous, [index]: true }))} />}
        </Pressable> : null;
      })}
    </ScrollView>
    <Modal visible={selected !== null} transparent={false} animationType="fade" onRequestClose={() => setSelected(null)}>
      <SafeAreaView style={styles.viewer}>
        <View style={styles.viewerHeader}>
          <Text style={styles.counter}>{current + 1} / {photos.length}</Text>
          <Pressable accessibilityRole="button" accessibilityLabel="关闭大图" onPress={() => setSelected(null)} hitSlop={12}><Ionicons name="close" size={28} color="white" /></Pressable>
        </View>
        {selected !== null ? <ScrollView
          key={`${selected}-${width}`} horizontal pagingEnabled showsHorizontalScrollIndicator={false}
          contentOffset={{ x: selected * width, y: 0 }}
          onMomentumScrollEnd={event => setCurrent(Math.round(event.nativeEvent.contentOffset.x / width))}
        >
          {photos.map((photo, index) => {
            const source = mobileApi.imageSource(photo);
            return <ScrollView key={photo.asset_id || photo.uri || index} style={{ width }} contentContainerStyle={[styles.zoomContent, { minHeight: height - 140 }]} maximumZoomScale={4} minimumZoomScale={1} centerContent>
              {source && !unavailable[index] ? <Image source={source} style={{ width, height: height - 140 }} resizeMode="contain" onError={() => setUnavailable(previous => ({ ...previous, [index]: true }))} /> : <Text style={styles.counter}>照片暂不可用</Text>}
            </ScrollView>;
          })}
        </ScrollView> : null}
      </SafeAreaView>
    </Modal>
  </>;
}

const styles = StyleSheet.create({
  thumbnails: { gap: spacing.sm, paddingVertical: spacing.sm },
  photo: { borderRadius: 12, backgroundColor: colors.surfaceMuted },
  compact: { width: 76, height: 76 }, large: { width: 220, height: 220 },
  missing: { alignItems: 'center', justifyContent: 'center', backgroundColor: colors.surfaceMuted, borderRadius: 12 },
  viewer: { flex: 1, backgroundColor: '#111' },
  viewerHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', padding: spacing.lg },
  counter: { color: 'white', fontSize: 15 }, zoomContent: { justifyContent: 'center', alignItems: 'center' },
});
