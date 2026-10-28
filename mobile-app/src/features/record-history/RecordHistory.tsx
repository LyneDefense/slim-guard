import { Ionicons } from '@expo/vector-icons';
import React, { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import { Card, SectionTitle } from '../../components/ui';
import { PhotoGallery } from '../../components/PhotoGallery';
import { colors, spacing } from '../../theme';
import { recordHistoryApi } from './api';
import { MealEditor } from './MealEditor';
import { mealLabels } from './types';
import type { HistoryRecord, RecordKind, RecordPage } from './types';

export function RecordHistory({ timezone, refreshKey, onChanged }: {
  timezone: string;
  refreshKey: unknown;
  onChanged: () => void;
}) {
  return <><SectionTitle title="记录历史" /><Card>
    <RecordSection kind="meal" title="饮食记录" timezone={timezone} refreshKey={refreshKey} onChanged={onChanged} />
    <RecordSection kind="exercise" title="运动记录" timezone={timezone} refreshKey={refreshKey} />
  </Card></>;
}

function RecordSection({ kind, title, timezone, refreshKey, onChanged }: {
  kind: RecordKind;
  title: string;
  timezone: string;
  refreshKey: unknown;
  onChanged?: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [page, setPage] = useState<RecordPage | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<HistoryRecord | null>(null);
  const [opening, setOpening] = useState<string | null>(null);
  const generation = useRef(0);
  const inFlight = useRef(false);
  useEffect(() => {
    generation.current += 1;
    inFlight.current = false;
    setPage(null); setBusy(false); setError(null);
    if (expanded) void load();
  }, [refreshKey, timezone, kind]);
  useEffect(() => { if (expanded && page === null) void load(); }, [expanded, refreshKey, timezone, kind]);
  useEffect(() => () => { generation.current += 1; }, []);
  async function load(cursor?: string) {
    if (inFlight.current) return;
    inFlight.current = true;
    const version = generation.current;
    setBusy(true); setError(null);
    try {
      const result = await recordHistoryApi.list(kind, timezone, cursor);
      if (version !== generation.current) return;
      setPage(previous => ({ ...result, records: cursor
        ? [...new Map([...(previous?.records || []), ...result.records].map(item => [item.ref.record_id, item])).values()]
        : result.records }));
    } catch { if (version === generation.current) setError('暂时无法加载，点击重试'); }
    finally { if (version === generation.current) { setBusy(false); inFlight.current = false; } }
  }
  async function openEditor(record: HistoryRecord) {
    if (opening) return;
    const version = generation.current;
    setOpening(record.ref.record_id); setError(null);
    try {
      const latest = await recordHistoryApi.meal(record.ref.record_id);
      if (version === generation.current) setEditing(latest);
    } catch (caught) {
      if (version === generation.current) setError(caught instanceof Error ? caught.message : '暂时无法打开记录');
    } finally { setOpening(null); }
  }
  function saved(record: HistoryRecord) {
    setPage(previous => previous ? { ...previous, records: previous.records.map(item => item.ref.record_id === record.ref.record_id ? record : item) } : previous);
    setEditing(null);
    onChanged?.();
  }
  return <View style={styles.section}>
    <Pressable accessibilityRole="button" accessibilityState={{ expanded }} onPress={() => setExpanded(!expanded)} style={styles.header}>
      <Text style={styles.title}>{title}</Text><Text style={styles.hint}>{expanded ? '收起' : '展开'}</Text>
    </Pressable>
    {expanded && <View>
      {page?.records.map(record => <RecordRow
        key={record.ref.record_id}
        record={record}
        timezone={timezone}
        onEdit={kind === 'meal' ? () => void openEditor(record) : undefined}
        opening={opening === record.ref.record_id}
      />)}
      {page?.records.length === 0 && <Text style={styles.hint}>暂无记录</Text>}
      {error && <Pressable onPress={() => void load()}><Text style={styles.hint}>{error}</Text></Pressable>}
      {busy ? <ActivityIndicator color={colors.primary} /> : page?.has_more && <Pressable onPress={() => void load(page.next_cursor || undefined)}><Text style={styles.more}>加载更早</Text></Pressable>}
    </View>}
    {editing ? <MealEditor key={editing.ref.record_id} record={editing} onClose={() => setEditing(null)} onSaved={saved} /> : null}
  </View>;
}

function RecordRow({ record, timezone, onEdit, opening }: {
  record: HistoryRecord;
  timezone: string;
  onEdit?: () => void;
  opening: boolean;
}) {
  const occurrence = record.occurrence;
  const time = occurrence.at ? new Date(occurrence.at).toLocaleString('zh-CN', { timeZone: timezone, year: 'numeric', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : occurrence.on || '日期未明确';
  const content = record.content;
  return <View style={styles.row}>
    <View style={styles.rowHeader}>
      <Text style={[styles.hint, styles.recordDate]}>{time}{content.kind === 'meal' ? ` · ${mealLabels[content.meal_type] || '其他'}` : ''}</Text>
      {content.kind === 'meal' && onEdit ? <Pressable
        accessibilityRole="button"
        accessibilityLabel="修改这条餐食记录"
        onPress={onEdit}
        disabled={opening}
        style={styles.editButton}
      >{opening ? <ActivityIndicator color={colors.primary} /> : <Ionicons name="pencil-outline" size={19} color={colors.primary} />}</Pressable> : null}
    </View>
    <Text style={styles.content}>{content.kind === 'meal'
      ? content.foods.map(food => `${food.name || '菜品未命名'}${food.portion ? `（${food.portion}）` : ''}`).join('、') || '暂无菜品'
      : `${content.activity_name}${content.duration_minutes !== null ? ` · ${content.duration_minutes} 分钟` : ''}${content.steps !== null ? ` · ${content.steps} 步` : ''}`}</Text>
    {content.kind === 'meal' && content.photo_asset_ids?.length ? <PhotoGallery compact photos={content.photo_asset_ids.map(asset_id => ({ asset_id, mime_type: 'image/jpeg' }))} /> : null}
  </View>;
}

const styles = StyleSheet.create({
  section: { paddingVertical: spacing.sm }, header: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: spacing.sm },
  rowHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: spacing.sm },
  recordDate: { flexShrink: 1 },
  title: { color: colors.ink, fontSize: 15, fontWeight: '600' }, hint: { color: colors.inkMuted, fontSize: 13 },
  editButton: { width: 44, height: 44, borderRadius: 22, backgroundColor: colors.primarySoft, alignItems: 'center', justifyContent: 'center', flexShrink: 0 },
  row: { paddingVertical: spacing.md, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.line },
  content: { color: colors.ink, fontSize: 14, marginVertical: spacing.sm }, more: { color: colors.primary, paddingVertical: spacing.md },
});
