import React from 'react';
import { Modal, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';

import { appearance as palette } from '../../shared/ui/appearance';
import { datedMeasurementDraft, measurementLabels, shortDate, weekday } from './presentation';
import type { DailyMeasurement, MeasurementMetric } from './types';

export function MeasurementDetail({ on, point, metric, onClose, openCoach }: {
  on: string; point?: DailyMeasurement; metric: MeasurementMetric; onClose: () => void; openCoach: (draft?: string) => void;
}) {
  const { label, unit } = measurementLabels[metric];
  return <Modal visible transparent animationType="slide" onRequestClose={onClose} statusBarTranslucent navigationBarTranslucent>
    <SafeAreaProvider>
      <View style={styles.overlay}>
        <Pressable accessibilityRole="button" accessibilityLabel="关闭测量详情" style={StyleSheet.absoluteFill} onPress={onClose} />
        <SafeAreaView edges={['top', 'left', 'right']} style={styles.positioner} pointerEvents="box-none">
          <View style={styles.sheet} accessibilityViewIsModal>
            <View style={styles.handle} />
            <ScrollView style={styles.scroll} contentContainerStyle={styles.body}>
              <Text accessibilityRole="header" style={styles.title}>{label}测量</Text>
              <Text style={styles.date}>{on.slice(0, 4)}年 {shortDate(on)} · {weekday(on)}</Text>
              <Text style={styles.value}>{point ? `${point.value.toFixed(1)} ${unit}` : '暂无记录'}</Text>
              <Text style={styles.hint}>{point ? '这是当天最新的一次测量。' : `这一天还没有${label}测量，可以补记。`}</Text>
            </ScrollView>
            <SafeAreaView edges={['bottom']} style={styles.footer}>
              <Pressable accessibilityRole="button" onPress={onClose} style={[styles.button, styles.close]}><Text style={styles.closeText}>关闭</Text></Pressable>
              {!point && <Pressable accessibilityRole="button" onPress={() => { onClose(); openCoach(datedMeasurementDraft(metric, on)); }} style={[styles.button, styles.add]}>
                <Text style={styles.addText}>补记这一天</Text>
              </Pressable>}
            </SafeAreaView>
          </View>
        </SafeAreaView>
      </View>
    </SafeAreaProvider>
  </Modal>;
}

const styles = StyleSheet.create({
  overlay: { flex: 1, backgroundColor: 'rgba(20,32,76,0.3)' }, positioner: { flex: 1, justifyContent: 'flex-end' },
  sheet: { maxHeight: '80%', backgroundColor: palette.background, borderTopLeftRadius: 24, borderTopRightRadius: 24 },
  handle: { alignSelf: 'center', width: 36, height: 4, backgroundColor: palette.line, borderRadius: 3, marginTop: 12 },
  scroll: { flexGrow: 0, flexShrink: 1 }, body: { padding: 24 }, title: { color: palette.ink, fontSize: 21, fontWeight: '700' },
  date: { color: palette.muted, fontSize: 14, lineHeight: 22, marginTop: 12 },
  value: { color: palette.ink, fontSize: 40, fontWeight: '700', marginVertical: 18 }, hint: { color: palette.muted, fontSize: 14, lineHeight: 22 },
  footer: { flexDirection: 'row', gap: 12, padding: 16, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: palette.line },
  button: { flex: 1, borderRadius: 16, minHeight: 48, alignItems: 'center', justifyContent: 'center' },
  close: { backgroundColor: palette.lavender }, closeText: { color: palette.ink, fontSize: 16, fontWeight: '600' },
  add: { backgroundColor: palette.indigo }, addText: { color: palette.surface, fontSize: 16, fontWeight: '600' },
});
