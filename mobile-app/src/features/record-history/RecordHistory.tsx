import React, { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import { Card, SectionTitle } from '../../components/ui';
import { mobileApi } from '../../lib/api';
import { colors, spacing } from '../../theme';
import { mealCorrectionDraft } from './correctionDraft';
import type { HistoryRecord, RecordKind, RecordPage } from './types';

const meals: Record<string, string> = { breakfast: '早餐', lunch: '午餐', dinner: '晚餐', snack: '加餐', unspecified: '饮食' };

export function RecordHistory({ timezone, refreshKey, onCorrect }: {
  timezone: string;
  refreshKey: unknown;
  onCorrect: (draft?: string) => void;
}) {
  return <><SectionTitle title="记录历史" /><Card>
    <RecordSection kind="meal" title="饮食记录" timezone={timezone} refreshKey={refreshKey} onCorrect={onCorrect} />
    <RecordSection kind="exercise" title="运动记录" timezone={timezone} refreshKey={refreshKey} />
  </Card></>;
}

function RecordSection({ kind, title, timezone, refreshKey, onCorrect }: {
  kind: RecordKind;
  title: string;
  timezone: string;
  refreshKey: unknown;
  onCorrect?: (draft?: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [page, setPage] = useState<RecordPage | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
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
      const result = await mobileApi.recordHistory(kind, timezone, cursor);
      if (version !== generation.current) return;
      setPage(previous => ({ ...result, records: cursor
        ? [...new Map([...(previous?.records || []), ...result.records].map(item => [item.ref.record_id, item])).values()]
        : result.records }));
    } catch { if (version === generation.current) setError('暂时无法加载，点击重试'); }
    finally { if (version === generation.current) { setBusy(false); inFlight.current = false; } }
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
        onCorrect={onCorrect}
      />)}
      {page?.records.length === 0 && <Text style={styles.hint}>暂无记录</Text>}
      {error && <Pressable onPress={() => void load()}><Text style={styles.hint}>{error}</Text></Pressable>}
      {busy ? <ActivityIndicator color={colors.primary} /> : page?.has_more && <Pressable onPress={() => void load(page.next_cursor || undefined)}><Text style={styles.more}>加载更早</Text></Pressable>}
    </View>}
  </View>;
}

function RecordRow({ record, timezone, onCorrect }: {
  record: HistoryRecord;
  timezone: string;
  onCorrect?: (draft?: string) => void;
}) {
  const occurrence = record.occurrence;
  const time = occurrence.at ? new Date(occurrence.at).toLocaleString('zh-CN', { timeZone: timezone, year: 'numeric', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : occurrence.on || '日期未明确';
  const content = record.content;
  return <View style={styles.row}>
    <View style={styles.rowHeader}>
      <Text style={styles.hint}>{time}{content.kind === 'meal' ? ` · ${meals[content.meal_type] || '饮食'}` : ''}</Text>
      {content.kind === 'meal' && onCorrect ? <Pressable
        accessibilityRole="button"
        accessibilityLabel="更正这条饮食记录"
        onPress={() => onCorrect(mealCorrectionDraft(record, timezone))}
        hitSlop={8}
      ><Text style={styles.correctAction}>更正</Text></Pressable> : null}
    </View>
    <Text style={styles.content}>{content.kind === 'meal'
      ? content.foods.map(food => `${food.name || '菜品未命名'}${food.portion ? `（${food.portion}）` : ''}`).join('、') || '暂无菜品'
      : `${content.activity_name}${content.duration_minutes !== null ? ` · ${content.duration_minutes} 分钟` : ''}${content.steps !== null ? ` · ${content.steps} 步` : ''}`}</Text>
  </View>;
}

const styles = StyleSheet.create({
  section: { paddingVertical: spacing.sm }, header: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: spacing.sm },
  rowHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: spacing.sm },
  title: { color: colors.ink, fontSize: 15, fontWeight: '600' }, hint: { color: colors.inkMuted, fontSize: 13 },
  correctAction: { color: colors.primary, fontSize: 13, fontWeight: '700', paddingVertical: 4, paddingHorizontal: 8 },
  row: { paddingVertical: spacing.md, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.line },
  content: { color: colors.ink, fontSize: 14, marginVertical: spacing.sm }, more: { color: colors.primary, paddingVertical: spacing.md },
});
