import { Ionicons } from '@expo/vector-icons';
import * as Crypto from 'expo-crypto';
import React, { useRef, useState } from 'react';
import { ActivityIndicator, KeyboardAvoidingView, Modal, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

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

  return <Modal visible animationType="slide" onRequestClose={() => { if (!saving) onClose(); }}>
    <SafeAreaView style={styles.safe}>
      <KeyboardAvoidingView style={styles.flex} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <View style={styles.header}>
          <Pressable disabled={saving} onPress={onClose} accessibilityRole="button"><Text style={styles.action}>取消</Text></Pressable>
          <Text style={styles.title}>修改餐食记录</Text>
          <Pressable disabled={saving || !valid} onPress={() => void save()} accessibilityRole="button" accessibilityLabel="保存餐食修改">
            {saving ? <ActivityIndicator color={colors.primary} /> : <Text style={[styles.action, (!valid || saving) && styles.disabled]}>保存</Text>}
          </Pressable>
        </View>
        <ScrollView contentContainerStyle={styles.body} keyboardShouldPersistTaps="handled">
          <Text style={styles.label}>餐次</Text>
          <View style={styles.types}>{(Object.entries(mealLabels) as [MealType, string][]).map(([value, label]) => <Pressable
            key={value} disabled={saving} accessibilityRole="radio" accessibilityState={{ checked: mealType === value }}
            onPress={() => setMealType(value)} style={[styles.chip, mealType === value && styles.selected]}
          ><Text style={mealType === value ? styles.selectedText : styles.chipText}>{label}</Text></Pressable>)}</View>
          <Text style={styles.label}>菜品</Text>
          {foods.map((food, index) => <View key={food.key} style={styles.foodRow}>
            <TextInput
              accessibilityLabel={`第 ${index + 1} 道菜的名称`} placeholder="填写菜名" value={food.name} maxLength={128}
              editable={!saving} onChangeText={name => setFoods(previous => previous.map(item => item.key === food.key ? { ...item, name } : item))}
              style={styles.foodInput}
            />
            <Pressable disabled={saving} accessibilityRole="button" accessibilityLabel={`删除第 ${index + 1} 道菜`} hitSlop={8}
              onPress={() => setFoods(previous => previous.filter(item => item.key !== food.key))}>
              <Ionicons name="trash-outline" color={colors.danger} size={21} />
            </Pressable>
          </View>)}
          <Pressable disabled={saving || foods.length >= 20} accessibilityRole="button" onPress={() => setFoods(previous => [...previous, { item_id: null, name: '', key: Crypto.randomUUID() }])} style={styles.add}>
            <Ionicons name="add-circle-outline" color={colors.primary} size={21} /><Text style={styles.action}>添加菜品</Text>
          </Pressable>
          {!foods.length ? <Text style={styles.hint}>请至少保留一道菜品。</Text> : null}
          {error ? <Text accessibilityRole="alert" style={styles.error}>{error}</Text> : null}
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  </Modal>;
}

const styles = StyleSheet.create({
  flex: { flex: 1 }, safe: { flex: 1, backgroundColor: colors.background },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', padding: spacing.lg, borderBottomWidth: StyleSheet.hairlineWidth, borderColor: colors.line },
  title: { color: colors.ink, fontWeight: '700', fontSize: 17 }, action: { color: colors.primary, fontWeight: '700', fontSize: 15 }, disabled: { opacity: 0.35 },
  body: { padding: spacing.lg, paddingBottom: 80 }, label: { color: colors.ink, fontSize: 15, fontWeight: '700', marginTop: spacing.lg, marginBottom: spacing.md },
  types: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm }, chip: { paddingVertical: 10, paddingHorizontal: 16, borderRadius: radius.pill, backgroundColor: colors.surfaceMuted },
  selected: { backgroundColor: colors.primary }, selectedText: { color: colors.white, fontWeight: '700' }, chipText: { color: colors.ink },
  foodRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.md, marginBottom: spacing.sm },
  foodInput: { flex: 1, color: colors.ink, fontSize: 16, backgroundColor: colors.surface, borderRadius: 12, padding: spacing.md, borderWidth: StyleSheet.hairlineWidth, borderColor: colors.line },
  add: { flexDirection: 'row', gap: spacing.sm, alignItems: 'center', paddingVertical: spacing.md },
  hint: { color: colors.inkMuted, fontSize: 13 }, error: { color: colors.danger, marginTop: spacing.md, lineHeight: 22 },
});
