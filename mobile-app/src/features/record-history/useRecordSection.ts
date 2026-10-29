import { useEffect, useRef, useState } from 'react';

import { recordHistoryApi } from './api';
import type { HistoryRecord, RecordKind, RecordPage } from './types';

export function useRecordSection(kind: RecordKind, timezone: string, refreshKey: unknown, expanded: boolean, onChanged?: () => void) {
  const [page, setPage] = useState<RecordPage | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<HistoryRecord | null>(null);
  const [opening, setOpening] = useState<string | null>(null);
  const generation = useRef(0);
  const inFlight = useRef(false);
  const editorInFlight = useRef(false);
  const retryCursor = useRef<string | undefined>(undefined);

  useEffect(() => {
    generation.current += 1;
    inFlight.current = false;
    editorInFlight.current = false;
    retryCursor.current = undefined;
    setPage(null); setBusy(false); setError(null); setOpening(null);
    if (expanded) void load();
  }, [refreshKey, timezone, kind]);
  useEffect(() => { if (expanded && page === null) void load(); }, [expanded, refreshKey, timezone, kind]);
  useEffect(() => () => { generation.current += 1; }, []);

  async function load(cursor?: string) {
    if (inFlight.current) return;
    inFlight.current = true;
    retryCursor.current = cursor;
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
    if (editorInFlight.current) return;
    editorInFlight.current = true;
    const version = generation.current;
    setOpening(record.ref.record_id); setError(null);
    try {
      const latest = await recordHistoryApi.meal(record.ref.record_id);
      if (version === generation.current) setEditing(latest);
    } catch (caught) {
      if (version === generation.current) setError(caught instanceof Error ? caught.message : '暂时无法打开记录');
    } finally {
      if (version === generation.current) { setOpening(null); editorInFlight.current = false; }
    }
  }

  function saved(record: HistoryRecord) {
    setPage(previous => previous ? { ...previous, records: previous.records.map(item => item.ref.record_id === record.ref.record_id ? record : item) } : previous);
    setEditing(null);
    onChanged?.();
  }

  return { page, busy, error, editing, opening, load, retry: () => load(retryCursor.current), openEditor, saved, closeEditor: () => setEditing(null) };
}
