import { useCallback, useEffect, useRef, useState } from 'react';

import type { ChatMessage } from '../../types';
import { mergeNotices, systemNoticeCandidates, type SystemNotice } from './noticeFeed';

export function useSystemNotices(messages: ChatMessage[] | undefined, userId: string | undefined) {
  const [notices, setNotices] = useState<SystemNotice[]>([]);
  const owner = useRef<string | undefined>(undefined);
  const seen = useRef(new Set<string>());
  useEffect(() => {
    const initial = owner.current !== userId;
    if (initial) { owner.current = userId; seen.current = new Set(); }
    if (!userId || !messages) { setNotices([]); return; }
    const candidates = systemNoticeCandidates(messages);
    const observed = new Set(seen.current);
    for (const candidate of candidates) seen.current.add(candidate.id);
    const now = Date.now();
    setNotices(previous => mergeNotices(initial ? [] : previous, candidates, observed, now, initial));
  }, [messages, userId]);
  useEffect(() => {
    const times = notices.flatMap(notice => notice.expiresAt === null ? [] : [notice.expiresAt]);
    if (!times.length) return;
    const timer = setTimeout(() => setNotices(previous => previous.filter(notice => notice.expiresAt === null || notice.expiresAt > Date.now())), Math.max(0, Math.min(...times) - Date.now()));
    return () => clearTimeout(timer);
  }, [notices]);
  const dismissNotice = useCallback((id: string) => setNotices(previous => previous.filter(notice => notice.id !== id)), []);
  return { notices, dismissNotice };
}
