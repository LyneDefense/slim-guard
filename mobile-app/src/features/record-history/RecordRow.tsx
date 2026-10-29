import { Ionicons } from '@expo/vector-icons';
import React from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import { PhotoGallery } from '../../components/PhotoGallery';
import { AppIcon } from '../../shared/ui/AppIcon';
import { appearance as palette } from '../../shared/ui/appearance';
import { mealBadgeColors, recordDate } from './presentation';
import { mealLabels } from './types';
import type { HistoryRecord } from './types';

export function RecordRow({ record, timezone, onEdit, opening }: {
  record: HistoryRecord; timezone: string; onEdit?: () => void; opening: boolean;
}) {
  const content = record.content;
  const date = recordDate(record.occurrence, timezone, content.kind === 'exercise');
  if (content.kind === 'exercise') return <View style={styles.row}>
    <Text style={styles.date}>{date}</Text>
    <View style={styles.exercise}>
      <Text style={styles.activity}>{content.activity_name}</Text>
      <View style={styles.chips}>
        {content.duration_minutes !== null && <View style={styles.chip}><AppIcon name="clock" size={16} color={palette.ink} /><Text style={styles.chipText}>{content.duration_minutes} 分钟</Text></View>}
        {content.steps !== null && <View style={styles.chip}><AppIcon name="steps" size={16} color={palette.ink} /><Text style={styles.chipText}>{content.steps.toLocaleString('en-US')} 步</Text></View>}
      </View>
    </View>
  </View>;

  const badge = mealBadgeColors[content.meal_type] || mealBadgeColors.unspecified;
  const photos = content.photo_asset_ids || [];
  const foodNames = content.foods.map(food => `${food.name || '菜品未命名'}${food.portion ? `（${food.portion}）` : ''}`);
  return <View style={styles.row}>
    <View style={styles.mealHeader}>
      <View style={styles.dateAndMeal}>
        <Text style={styles.date}>{date}</Text>
        <View style={[styles.mealBadge, { backgroundColor: badge.backgroundColor }]}><Text style={[styles.mealLabel, { color: badge.color }]}>{mealLabels[content.meal_type] || '其他'}</Text></View>
      </View>
      {onEdit && <Pressable accessibilityRole="button" accessibilityLabel="修改这条餐食记录" onPress={onEdit} disabled={opening} style={styles.editButton}>
        <View style={styles.editFace}>{opening ? <ActivityIndicator color={palette.indigo} /> : <Ionicons name="pencil-outline" size={20} color={palette.ink} />}</View>
      </Pressable>}
    </View>
    <View style={[styles.mealBody, photos.length > 0 && styles.withPhoto]}>
      {photos.length > 0 && <PhotoGallery variant="record" photos={photos.map(asset_id => ({ asset_id, mime_type: 'image/jpeg' }))} />}
      {photos.length > 0 ? <View style={styles.photoFoods} accessible accessibilityLabel={foodNames.join('、') || '暂无菜品'}>
        {foodNames.length ? foodNames.map((name, index) => <Text key={content.foods[index].item_id} style={[styles.foods, styles.photoFood]}>{name}{index < foodNames.length - 1 ? '、' : ''}</Text>) : <Text style={styles.foods}>暂无菜品</Text>}
      </View> : <Text style={styles.foods}>{foodNames.join('、') || '暂无菜品'}</Text>}
    </View>
  </View>;
}

const styles = StyleSheet.create({
  row: { paddingVertical: 11, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: palette.line },
  date: { color: '#6D758B', fontSize: 14, lineHeight: 21 },
  mealHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 8, minHeight: 24 },
  dateAndMeal: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 10, flex: 1 },
  mealBadge: { paddingHorizontal: 10, paddingVertical: 3, borderRadius: 20 },
  mealLabel: { fontSize: 12, lineHeight: 17, fontWeight: '500' },
  editButton: { width: 44, height: 44, marginVertical: -10, alignItems: 'center', justifyContent: 'center' },
  editFace: { width: 32, height: 32, borderRadius: 16, backgroundColor: palette.lavender, alignItems: 'center', justifyContent: 'center' },
  mealBody: { marginTop: 8 },
  withPhoto: { flexDirection: 'row', alignItems: 'center', gap: 20 },
  foods: { flexShrink: 1, color: palette.ink, fontSize: 15, lineHeight: 23, fontWeight: '500' },
  photoFoods: { flex: 1, minWidth: 0, maxWidth: 200, flexDirection: 'row', flexWrap: 'wrap' },
  photoFood: { flexShrink: 0, maxWidth: '100%', fontSize: 16, lineHeight: 24 },
  exercise: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', columnGap: 12, rowGap: 8, marginTop: 5 },
  activity: { color: palette.ink, fontSize: 16, lineHeight: 24, fontWeight: '600', minWidth: 68, flexShrink: 1 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 10 },
  chip: { flexDirection: 'row', alignItems: 'center', gap: 6, backgroundColor: palette.lavender, borderRadius: 20, paddingHorizontal: 10, paddingVertical: 5 },
  chipText: { color: palette.ink, fontSize: 13, lineHeight: 18 },
});
