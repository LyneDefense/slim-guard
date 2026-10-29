import { Ionicons } from '@expo/vector-icons';
import React, { useState } from 'react';
import { Keyboard, Modal, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';

import { appearance as palette } from './appearance';
import { SettingsAction, settingsStyles } from './SettingsPrimitives';

export function ChoicePicker<T extends string>({ label, value, options, onChange, disabled = false, allowClear = false }: {
  label: string; value: T | null; options: readonly { value: T; label: string }[]; onChange: (value: T | null) => void; disabled?: boolean; allowClear?: boolean;
}) {
  const [open, setOpen] = useState(false);
  return <View>
    <Text style={settingsStyles.label}>{label}</Text>
    <Pressable accessibilityRole="button" accessibilityLabel={label} accessibilityState={{ expanded: open, disabled }} disabled={disabled}
      onPress={() => { Keyboard.dismiss(); setOpen(true); }} style={styles.input}>
      <Text style={[styles.value, !value && styles.placeholder]}>{options.find(option => option.value === value)?.label || '请选择'}</Text>
      <Ionicons name="chevron-down" size={16} color={palette.muted} />
    </Pressable>
    {open && <Modal visible transparent animationType="slide" onRequestClose={() => setOpen(false)}>
      <SafeAreaProvider><View style={styles.overlay}>
        <Pressable accessibilityRole="button" accessibilityLabel={`取消选择${label}`} style={StyleSheet.absoluteFill} onPress={() => setOpen(false)} />
        <SafeAreaView edges={['top', 'left', 'right']} style={styles.positioner} pointerEvents="box-none">
          <View style={styles.sheet} accessibilityViewIsModal>
            <Text accessibilityRole="header" style={styles.title}>{label}</Text>
            <ScrollView>
              {options.map(option => <Pressable key={option.value} accessibilityRole="radio" accessibilityState={{ checked: value === option.value }}
                onPress={() => { onChange(option.value); setOpen(false); }} style={styles.option}>
                <Text style={styles.value}>{option.label}</Text>{value === option.value && <Ionicons name="checkmark" size={20} color={palette.indigo} />}
              </Pressable>)}
              {allowClear && <Pressable accessibilityRole="button" onPress={() => { onChange(null); setOpen(false); }} style={styles.option}><Text style={styles.placeholder}>暂不填写</Text></Pressable>}
            </ScrollView>
            <SafeAreaView edges={['bottom']} style={styles.footer}><SettingsAction title="取消" variant="secondary" onPress={() => setOpen(false)} /></SafeAreaView>
          </View>
        </SafeAreaView>
      </View></SafeAreaProvider>
    </Modal>}
  </View>;
}

const styles = StyleSheet.create({
  input: { ...settingsStyles.input, flexDirection: 'row', alignItems: 'center', gap: 6 }, value: { color: palette.ink, fontSize: 15, flex: 1 },
  placeholder: { color: palette.muted, fontSize: 15 }, overlay: { flex: 1, backgroundColor: 'rgba(20,32,76,0.3)' },
  positioner: { flex: 1, justifyContent: 'flex-end' }, sheet: { maxHeight: '78%', backgroundColor: palette.background, borderTopLeftRadius: 24, borderTopRightRadius: 24, paddingTop: 16 },
  title: { color: palette.ink, fontSize: 18, fontWeight: '700', textAlign: 'center', paddingBottom: 12 },
  option: { minHeight: 48, paddingHorizontal: 24, paddingVertical: 12, flexDirection: 'row', alignItems: 'center', borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: palette.line },
  footer: { padding: 16 },
});
