import { useEffect, useRef, useState } from 'react';
import { AppState } from 'react-native';

import { dailyInsightsApi } from './api';
import type { DailyInsights } from './types';

// A navigation cache, not another authority. Keys include owner/date/timezone;
// all evidence freshness decisions remain on the server.
const cache = new Map<string, DailyInsights>();
export function localDay(timezone: string, now = new Date()): string {
  const parts = new Intl.DateTimeFormat('en', { timeZone: timezone,
    year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(now);
  const part = (type: string) => parts.find(item => item.type === type)?.value || '';
  return `${part('year')}-${part('month')}-${part('day')}`;
}

export function useDailyInsights(userId: string | undefined, timezone: string, refreshKey: unknown) {
  const [entry, setEntry] = useState<{ key: string; value: DailyInsights } | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const [verifying, setVerifying] = useState(true);
  const reload = useRef<(retry?: boolean) => Promise<void>>(async () => {});

  useEffect(() => {
    let active = true, inFlight = false, followUp = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const key = () => `${userId}:${timezone}:${localDay(timezone)}`;
    const clear = () => { if (timer) clearTimeout(timer); timer = null; };
    async function load(retry = false) {
      if (!active || !userId || AppState.currentState === 'background') return;
      if (inFlight) { followUp = true; return; }
      inFlight = true; clear(); setBusy(true);
      let interval = 45_000;
      const requestedKey = key();
      try {
        const value = await (retry ? dailyInsightsApi.retry() : dailyInsightsApi.get());
        if (!active || requestedKey !== key()) return;
        if (value.date !== localDay(value.timezone) || value.timezone !== timezone) {
          setEntry(null); setError('日期已变化，请刷新首页'); return;
        }
        cache.set(`${userId}:${timezone}`, value);
        setEntry({ key: requestedKey, value }); setError(null); setVerifying(false);
        if (value.status === 'pending' || value.status === 'running') interval = 3_000;
      } catch {
        if (active) { setError('暂时无法加载今日总结'); setVerifying(false); }
      } finally {
        inFlight = false;
        if (active) {
          setBusy(false);
          timer = setTimeout(() => { timer = null; void load(); }, followUp ? 0 : interval);
          followUp = false;
        }
      }
    }
    reload.current = load;
    const saved = cache.get(`${userId}:${timezone}`);
    setEntry(saved?.date === localDay(timezone) ? { key: key(), value: saved } : null);
    setVerifying(true); setError(null);
    void load();
    const foreground = AppState.addEventListener('change', state => {
      if (state === 'active') { setVerifying(true); void load(); } else clear();
    });
    return () => { active = false; clear(); foreground.remove(); };
  }, [userId, timezone, refreshKey]);
  const key = `${userId}:${timezone}:${localDay(timezone)}`;
  const view = entry?.key === key ? entry.value : null;
  const refresh = () => { setVerifying(true); return reload.current(); };
  return { view, busy, error, verifying, refresh, retry: () => reload.current(true) };
}
