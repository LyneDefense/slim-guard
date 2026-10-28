import { Ionicons } from '@expo/vector-icons';
import { StatusBar } from 'expo-status-bar';
import React, { useMemo, useRef, useState } from 'react';
import { Animated, Image, Modal, PanResponder, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import type { ImageSourcePropType, LayoutChangeEvent, NativeScrollEvent, NativeSyntheticEvent } from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';

import { mobileApi } from '../lib/api';
import { spacing } from '../theme';
import type { ChatAttachment } from '../types';

type Props = {
  photos: ChatAttachment[];
  initialIndex: number;
  onClose: () => void;
};

export function PhotoViewer({ photos, initialIndex, onClose }: Props) {
  const [current, setCurrent] = useState(initialIndex);
  const [viewport, setViewport] = useState({ width: 0, height: 0 });
  const [zoomedPages, setZoomedPages] = useState<Record<number, boolean>>({});

  function measure(event: LayoutChangeEvent) {
    const { width, height } = event.nativeEvent.layout;
    if (width > 0 && height > 0 && (width !== viewport.width || height !== viewport.height)) {
      setViewport({ width, height });
      setZoomedPages({});
    }
  }

  return (
    <Modal visible animationType="fade" statusBarTranslucent navigationBarTranslucent onRequestClose={onClose}>
      <SafeAreaProvider>
        <StatusBar style="light" />
        <SafeAreaView style={styles.viewer}>
          <View style={styles.viewport} onLayout={measure}>
            {viewport.width > 0 && viewport.height > 0 ? (
              <ScrollView
                key={`${viewport.width}-${viewport.height}`}
                horizontal pagingEnabled showsHorizontalScrollIndicator={false}
                scrollEnabled={!zoomedPages[current]} bounces={false}
                contentInsetAdjustmentBehavior="never"
                contentOffset={{ x: current * viewport.width, y: 0 }}
                onMomentumScrollEnd={event => setCurrent(Math.max(0, Math.min(photos.length - 1, Math.round(event.nativeEvent.contentOffset.x / viewport.width))))}
              >
                {photos.map((photo, index) => (
                  <ZoomablePhoto
                    key={`${photo.asset_id || photo.uri || index}-${viewport.width}-${viewport.height}`}
                    source={mobileApi.imageSource(photo)} width={viewport.width} height={viewport.height}
                    onClose={onClose}
                    onZoomChange={zoomed => setZoomedPages(previous => previous[index] === zoomed ? previous : { ...previous, [index]: zoomed })}
                  />
                ))}
              </ScrollView>
            ) : null}
          </View>
          <View style={styles.footer}>
            <Text style={styles.counter}>{current + 1} / {photos.length}</Text>
            <Pressable accessibilityRole="button" accessibilityLabel="关闭大图" onPress={onClose} style={styles.closeButton}>
              <Ionicons name="close" size={22} color="white" />
              <Text style={styles.closeText}>关闭</Text>
            </Pressable>
            <Text style={styles.hint}>双指缩放 · 下滑关闭</Text>
          </View>
        </SafeAreaView>
      </SafeAreaProvider>
    </Modal>
  );
}

function ZoomablePhoto({ source, width, height, onClose, onZoomChange }: {
  source: ImageSourcePropType | null;
  width: number;
  height: number;
  onClose: () => void;
  onZoomChange: (zoomed: boolean) => void;
}) {
  const [unavailable, setUnavailable] = useState(false);
  const scale = useRef(1);
  const blocked = useRef(false);
  const drag = useRef(new Animated.Value(0)).current;
  const reset = () => Animated.spring(drag, { toValue: 0, useNativeDriver: true }).start();
  const responder = useMemo(() => PanResponder.create({
    onStartShouldSetPanResponderCapture: (_event, gesture) => {
      blocked.current = gesture.numberActiveTouches !== 1;
      return false;
    },
    onMoveShouldSetPanResponderCapture: (_event, gesture) => {
      if (gesture.numberActiveTouches !== 1) blocked.current = true;
      return !blocked.current && scale.current <= 1.01 && gesture.numberActiveTouches === 1
        && gesture.dy > 12 && gesture.dy > Math.abs(gesture.dx) * 1.5;
    },
    onPanResponderMove: (_event, gesture) => {
      if (gesture.numberActiveTouches !== 1) blocked.current = true;
      drag.setValue(blocked.current ? 0 : Math.max(0, gesture.dy));
    },
    onPanResponderRelease: (_event, gesture) => {
      if (!blocked.current && scale.current <= 1.01
        && (gesture.dy > Math.max(64, height * 0.14) || (gesture.dy > 24 && gesture.vy > 0.8))) onClose();
      else reset();
    },
    onPanResponderTerminate: reset,
    onPanResponderTerminationRequest: () => true,
  }), [drag, height, onClose]);

  function trackZoom(event: NativeSyntheticEvent<NativeScrollEvent>) {
    const zoom = event.nativeEvent.zoomScale;
    if (typeof zoom === 'number' && zoom > 0) {
      scale.current = zoom;
      onZoomChange(zoom > 1.01);
    }
  }

  return (
    <Animated.View style={{ width, height, transform: [{ translateY: drag }] }} {...responder.panHandlers}>
      <ScrollView
        style={{ width, height }} contentContainerStyle={styles.photoContent}
        maximumZoomScale={4} minimumZoomScale={1} centerContent bounces={false} bouncesZoom={false}
        contentInsetAdjustmentBehavior="never" automaticallyAdjustContentInsets={false}
        onScroll={trackZoom} scrollEventThrottle={16}
      >
        {source && !unavailable
          ? <Image source={source} style={{ width, height }} resizeMode="contain" onError={() => setUnavailable(true)} />
          : <View style={[styles.missing, { width, height }]}><Text style={styles.counter}>照片暂不可用</Text></View>}
      </ScrollView>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  viewer: { flex: 1, backgroundColor: '#111' },
  viewport: { flex: 1 }, photoContent: { alignItems: 'center', justifyContent: 'center' },
  missing: { alignItems: 'center', justifyContent: 'center' },
  footer: { alignItems: 'center', paddingHorizontal: spacing.lg, paddingVertical: spacing.md, gap: spacing.sm, flexShrink: 0 },
  counter: { color: '#E5E5E5', fontSize: 14 },
  closeButton: { minWidth: 144, minHeight: 48, borderRadius: 24, backgroundColor: '#333', flexDirection: 'row', gap: spacing.sm, justifyContent: 'center', alignItems: 'center' },
  closeText: { color: 'white', fontSize: 16, fontWeight: '700' },
  hint: { color: '#AAA', fontSize: 12 },
});
