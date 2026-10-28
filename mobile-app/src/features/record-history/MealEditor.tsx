import { Ionicons } from '@expo/vector-icons';
import * as Crypto from 'expo-crypto';
import React, { useRef, useState } from 'react';
import { ActivityIndicator, Alert, KeyboardAvoidingView, Modal, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';

import { colors, radius, spacing } from '../../theme';
import { recordHistoryApi } from './api';
import { mealLabels, type HistoryRecord, type MealType } from './types';

export function MealEditor({ record, onClose, onSaved }: {
  record: HistoryRecord; onClose: () => void; onSaved: (saved: HistoryRecord) => void;
}) {
  const content = record.content;
  const [mealType, setMealType] = useState<MealType>(content.kind === 'meal' ? content.meal_type : 'unspecified');
  const [foods, setFoods] = useState(content.kind === 'meal' ? content.foods.map(food => ({ item_id: food.item_id as string | null, name: food.name || '', key: food.item_id })) : []);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const attempt = useRef<{ signature: string; id: string } | null>(null);
  const valid = foods.length > 0 && foods.every(food => food.name.trim());
  const originalFoods = content.kind === 'meal' ? content.foods : [];
  const dirty = mealType !== (content.kind === 'meal' ? content.meal_type : 'unspecified')
    || foods.length !== originalFoods.length
    || foods.some((food, index) => food.item_id !== originalFoods[index]?.item_id || food.name.trim() !== (originalFoods[index]?.name || '').trim());

  function requestClose() {
    if (saving) return;
    if (!dirty) { onClose(); return; }
    Alert.alert('放弃修改？', '修改尚未保存，是否放弃？', [
      { text: '继续修改', style: 'cancel' },
      { text: '放弃修改', style: 'destructive', onPress: onClose },
    ]);
  }

  async function save() {
    if (saving || !valid) return;
    const edit = { expected_revision: record.ref.revision, meal_type: mealType, foods: foods.map(food => ({ item_id: food.item_id, name: food.name.trim() })) };
    const signature = JSON.stringify(edit);
    if (attempt.current?.signature !== signature) attempt.current = { signature, id: Crypto.randomUUID() };
    setSaving(true); setError(null);
    try {
      const saved = await recordHistoryApi.editMeal(record.ref.record_id, { ...edit, operation_id: attempt.current.id });
      onSaved(saved);
    } catch (caught) { setError(caught instanceof Error ? caught.message : '保存失败，请重试'); }
    finally { setSaving(false); }
  }

  return <Modal visible transparent animationType="slide" statusBarTranslucent navigationBarTranslucent onRequestClose={requestClose}>
    <SafeAreaProvider>
      <View style={styles.overlay}>
        <Pressable accessibilityRole="button" accessibilityLabel="关闭餐食编辑" disabled={saving} onPress={requestClose} style={styles.backdrop} />
        <SafeAreaView edges={['top', 'left', 'right']} style={styles.flex} pointerEvents="box-none">
          <KeyboardAvoidingView style={styles.positioner} behavior={Platform.OS === 'ios' ? 'padding' : 'height'} pointerEvents="box-none">
            <View style={styles.sheet} accessibilityViewIsModal>
              <View style={styles.header}>
                <View style={styles.handle} />
                <Text accessibilityRole="header" style={styles.title}>修改餐食</Text>
              </View>
              <ScrollView style={styles.scroll} contentContainerStyle={styles.body} keyboardShouldPersistTaps="handled" keyboardDismissMode="on-drag" contentInsetAdjustmentBehavior="never">
                <Text style={styles.label}>餐次</Text>
                <View style={styles.types}>
                  {(Object.entries(mealLabels) as [MealType, string][]).map(([value, label]) => (
                    <Pressable
                      key={value} disabled={saving} accessibilityRole="radio" accessibilityState={{ checked: mealType === value }}
                      onPress={() => setMealType(value)} style={[styles.chip, mealType === value && styles.selected]}
                    ><Text style={mealType === value ? styles.selectedText : styles.chipText}>{label}</Text></Pressable>
                  ))}
                </View>
                <Text style={styles.label}>菜品</Text>
                {foods.map((food, index) => (
                  <View key={food.key} style={styles.foodRow}>
                    <TextInput
                      accessibilityLabel={`第 ${index + 1} 道菜的名称`} placeholder="填写菜名" value={food.name} maxLength={128}
                      editable={!saving} onChangeText={name => setFoods(previous => previous.map(item => item.key === food.key ? { ...item, name } : item))}
                      style={styles.foodInput}
                    />
                    <Pressable disabled={saving} accessibilityRole="button" accessibilityLabel={`删除第 ${index + 1} 道菜`} style={styles.deleteButton}
                      onPress={() => setFoods(previous => previous.filter(item => item.key !== food.key))}>
                      <Ionicons name="trash-outline" color={colors.danger} size={21} />
                    </Pressable>
                  </View>
                ))}
                <Pressable disabled={saving || foods.length >= 20} accessibilityRole="button" onPress={() => setFoods(previous => [...previous, { item_id: null, name: '', key: Crypto.randomUUID() }])} style={styles.add}>
                  <Ionicons name="add-circle-outline" color={colors.primary} size={21} /><Text style={styles.action}>添加菜品</Text>
                </Pressable>
                {!foods.length ? <Text style={styles.hint}>请至少保留一道菜品。</Text> : null}
                {error ? <Text accessibilityRole="alert" style={styles.error}>{error}</Text> : null}
              </ScrollView>
              <SafeAreaView edges={['bottom']} style={styles.footer}>
                <View style={styles.actions}>
                  <Pressable disabled={saving} onPress={requestClose} accessibilityRole="button" accessibilityLabel="取消餐食修改" style={[styles.button, styles.cancelButton, saving && styles.disabled]}>
                    <Text style={styles.cancelText}>取消</Text>
                  </Pressable>
                  <Pressable disabled={saving || !valid} onPress={() => void save()} accessibilityRole="button" accessibilityLabel="保存餐食修改" accessibilityState={{ disabled: saving || !valid, busy: saving }} style={[styles.button, styles.saveButton, (!valid || saving) && styles.disabled]}>
                    {saving ? <ActivityIndicator color={colors.white} /> : <Text style={styles.saveText}>保存修改</Text>}
                  </Pressable>
                </View>
              </SafeAreaView>
            </View>
          </KeyboardAvoidingView>
        </SafeAreaView>
      </View>
    </SafeAreaProvider>
  </Modal>;
}

const styles = StyleSheet.create({
  flex: { flex: 1 }, overlay: { flex: 1, backgroundColor: 'rgba(21, 36, 27, 0.4)' },
  backdrop: { ...StyleSheet.absoluteFill }, positioner: { flex: 1, justifyContent: 'flex-end' },
  sheet: { maxHeight: '85%', backgroundColor: colors.background, borderTopLeftRadius: radius.lg, borderTopRightRadius: radius.lg, overflow: 'hidden' },
  header: { alignItems: 'center', paddingHorizontal: spacing.lg, paddingBottom: spacing.lg, flexShrink: 0, borderBottomWidth: StyleSheet.hairlineWidth, borderColor: colors.line },
  handle: { width: 36, height: 4, borderRadius: 2, backgroundColor: colors.line, marginTop: spacing.md, marginBottom: spacing.md },
  title: { color: colors.ink, fontWeight: '700', fontSize: 17 }, action: { color: colors.primary, fontWeight: '700', fontSize: 15 }, disabled: { opacity: 0.35 },
  scroll: { flexShrink: 1 }, body: { paddingHorizontal: spacing.lg, paddingBottom: spacing.md }, label: { color: colors.ink, fontSize: 15, fontWeight: '700', marginTop: spacing.lg, marginBottom: spacing.md },
  footer: { backgroundColor: colors.surface, padding: spacing.lg, borderTopWidth: StyleSheet.hairlineWidth, borderColor: colors.line, flexShrink: 0 },
  actions: { flexDirection: 'row', gap: spacing.md }, button: { minHeight: 48, alignItems: 'center', justifyContent: 'center', borderRadius: radius.md },
  cancelButton: { flex: 1, backgroundColor: colors.surfaceMuted }, saveButton: { flex: 2, backgroundColor: colors.primary },
  cancelText: { color: colors.ink, fontSize: 16, fontWeight: '600' }, saveText: { color: colors.white, fontSize: 16, fontWeight: '700' },
  types: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm }, chip: { minHeight: 44, justifyContent: 'center', paddingVertical: 10, paddingHorizontal: 16, borderRadius: radius.pill, backgroundColor: colors.surfaceMuted },
  selected: { backgroundColor: colors.primary }, selectedText: { color: colors.white, fontWeight: '700' }, chipText: { color: colors.ink },
  foodRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.md, marginBottom: spacing.sm },
  foodInput: { flex: 1, color: colors.ink, fontSize: 16, backgroundColor: colors.surface, borderRadius: 12, padding: spacing.md, borderWidth: StyleSheet.hairlineWidth, borderColor: colors.line },
  deleteButton: { width: 44, height: 44, alignItems: 'center', justifyContent: 'center' },
  add: { minHeight: 48, flexDirection: 'row', gap: spacing.sm, alignItems: 'center', paddingVertical: spacing.md },
  hint: { color: colors.inkMuted, fontSize: 13 }, error: { color: colors.danger, marginTop: spacing.md, lineHeight: 22 },
});
