import DateTimePicker from '@expo/ui/community/datetime-picker';
import { Ionicons } from '@expo/vector-icons';
import React, { useState } from 'react';
import { Keyboard, Modal, Platform, Pressable, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { dateText, displayDate, initialPickerDate } from '../lib/dates';
import { appearance as colors } from '../shared/ui/appearance';

const radius = { md: 10, lg: 24 };
const spacing = { sm: 6, md: 12, lg: 16 };

type Props = {
  label: string;
  value: string;
  onChange: (value: string) => void;
  minimumDate?: Date;
  maximumDate?: Date;
  compact?: boolean;
  disabled?: boolean;
};

export function DateField({ label, value, onChange, minimumDate, maximumDate, compact = false, disabled = false }: Props) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(() => initialPickerDate(value, minimumDate, maximumDate));

  function showPicker() {
    if (disabled) return;
    Keyboard.dismiss();
    setDraft(initialPickerDate(value, minimumDate, maximumDate));
    setOpen(true);
  }

  function confirm(selected: Date) {
    onChange(dateText(initialPickerDate(dateText(selected), minimumDate, maximumDate)));
    setOpen(false);
  }

  return (
    <View>
      <Text style={styles.fieldLabel}>{label}</Text>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={label}
        accessibilityState={{ expanded: open, disabled }} disabled={disabled}
        onPress={showPicker}
        style={styles.dateButton}
      >
        <Text style={[styles.dateText, !value && styles.datePlaceholder]}>
          {value ? compact ? value.replaceAll('-', '/') : displayDate(value) : '请选择日期'}
        </Text>
        <Ionicons name="calendar-outline" size={compact ? 16 : 19} color={colors.indigo} />
      </Pressable>

      {open && Platform.OS === 'ios' ? (
        <Modal transparent animationType="fade" onRequestClose={() => setOpen(false)}>
          <View style={styles.modal}>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="取消选择日期"
              style={styles.backdrop}
              onPress={() => setOpen(false)}
            />
            <SafeAreaView edges={['bottom']} style={styles.sheet} accessibilityViewIsModal>
              <View style={styles.toolbar}>
                <Pressable accessibilityRole="button" onPress={() => setOpen(false)} style={styles.action}>
                  <Text style={styles.cancelText}>取消</Text>
                </Pressable>
                <Text style={styles.title}>{label}</Text>
                <Pressable accessibilityRole="button" onPress={() => confirm(draft)} style={styles.action}>
                  <Text style={styles.confirmText}>确定</Text>
                </Pressable>
              </View>
              <Text style={styles.preview}>{displayDate(dateText(draft))}</Text>
              <DateTimePicker
                value={draft}
                mode="date"
                display="spinner"
                locale="zh_CN"
                themeVariant="light"
                accentColor={colors.indigo}
                minimumDate={minimumDate}
                maximumDate={maximumDate}
                style={styles.picker}
                onValueChange={(_event, selected) => setDraft(selected)}
              />
            </SafeAreaView>
          </View>
        </Modal>
      ) : open ? (
        <DateTimePicker
          value={draft}
          mode="date"
          presentation="dialog"
          minimumDate={minimumDate}
          maximumDate={maximumDate}
          accentColor={colors.indigo}
          positiveButton={{ label: '确定' }}
          negativeButton={{ label: '取消' }}
          onDismiss={() => setOpen(false)}
          onValueChange={(_event, selected) => confirm(selected)}
        />
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  fieldLabel: { color: colors.muted, fontSize: 12, lineHeight: 18, marginBottom: 5 },
  dateButton: { minHeight: 44, paddingHorizontal: 10, flexDirection: 'row', alignItems: 'center', gap: spacing.sm, borderRadius: radius.md, backgroundColor: '#F6F5FE', borderWidth: 1, borderColor: '#E5E6FA' },
  dateText: { flex: 1, color: colors.ink, fontSize: 13, fontWeight: '500' },
  datePlaceholder: { color: '#949B96', fontWeight: '500' },
  modal: { flex: 1, justifyContent: 'flex-end' },
  backdrop: { ...StyleSheet.absoluteFill, backgroundColor: 'rgba(21, 36, 27, 0.35)' },
  sheet: { width: '100%', backgroundColor: colors.surface, borderTopLeftRadius: radius.lg, borderTopRightRadius: radius.lg, paddingHorizontal: spacing.lg, paddingBottom: spacing.md },
  toolbar: { minHeight: 56, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.line },
  action: { minWidth: 56, minHeight: 48, alignItems: 'center', justifyContent: 'center' },
  cancelText: { color: colors.muted, fontSize: 15 },
  confirmText: { color: colors.indigo, fontSize: 15, fontWeight: '800' },
  title: { flex: 1, textAlign: 'center', color: colors.ink, fontSize: 16, fontWeight: '800' },
  preview: { color: colors.muted, fontSize: 14, textAlign: 'center', marginTop: spacing.md },
  picker: { width: '100%', height: 216 },
});
