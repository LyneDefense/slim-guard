import { useEffect, useState } from 'react';

import { progressApi } from './api';
import type { PeriodDays, PeriodProgress } from './types';

type Snapshot = {
  key: string;
  data: PeriodProgress | null;
  error: boolean;
  loading: boolean;
};

export function usePeriodProgress(userId: string | undefined, timezone: string | undefined, refreshKey: unknown) {
  const [days, setDays] = useState<PeriodDays>(7);
  const [retry, setRetry] = useState(0);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const key = `${userId}|${timezone}|${days}`;

  useEffect(() => {
    if (!userId || !timezone) return;
    let active = true;
    setSnapshot({ key, data: null, error: false, loading: true });
    void progressApi.period(days).then(data => {
      if (active) setSnapshot({ key, data, error: false, loading: false });
    }).catch(() => {
      if (active) setSnapshot({ key, data: null, error: true, loading: false });
    });
    return () => { active = false; };
  }, [key, days, userId, timezone, refreshKey, retry]);

  const current = snapshot?.key === key ? snapshot : null;
  return {
    days, setDays,
    data: current?.data ?? null,
    error: current?.error ?? false,
    loading: !!userId && (current?.loading ?? true),
    reload: () => setRetry(value => value + 1),
  };
}
