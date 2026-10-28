import type { ChatMessage } from '../../types';

export type SystemNotice = { id: string; turnId: string; text: string; requiresResponse: boolean; createdAt: number; expiresAt: number | null };

export function systemNoticeCandidates(messages: ChatMessage[]): Omit<SystemNotice, 'expiresAt'>[] {
  const byTurn = new Map<string, { message: ChatMessage; index: number }>();
  let lastUser = -1;
  messages.forEach((message, index) => {
    if (message.participant === 'user' || message.role === 'user') { lastUser = index; return; }
    if (message.participant !== 'system_assistant') return;
    const key = message.turn_id || message.id;
    const previous = byTurn.get(key);
    // A projected card and its system explanation describe the same operation.
    // Prefer the model's notice, preserving only one notification for the Turn.
    if (!previous || (previous.message.kind === 'card' && message.kind === 'text')) byTurn.set(key, { message, index });
  });
  return [...byTurn.entries()].flatMap(([turnId, { message, index }]) => {
    if (message.requires_response && index < lastUser) return [];
    const text = message.notice_text || message.text || (message.card ? '记录已更新，可在记录历史中查看。' : '');
    if (!text.trim()) return [];
    return [{ id: `notice-${turnId}`, turnId, text: text.trim(), requiresResponse: message.requires_response === true, createdAt: Date.parse(message.created_at) }];
  });
}

export function mergeNotices(
  current: SystemNotice[], candidates: ReturnType<typeof systemNoticeCandidates>, seen: Set<string>, now: number, initial: boolean,
): SystemNotice[] {
  const activeQuestions = new Set(candidates.filter(item => item.requiresResponse).map(item => item.id));
  const retained = current.filter(item => item.requiresResponse ? activeQuestions.has(item.id) : item.expiresAt !== null && item.expiresAt > now);
  for (const candidate of candidates) {
    if (seen.has(candidate.id)) continue;
    if (initial && !candidate.requiresResponse && (!Number.isFinite(candidate.createdAt) || now - candidate.createdAt > 10_000)) continue;
    retained.push({ ...candidate, expiresAt: candidate.requiresResponse ? null : now + 10_000 });
  }
  return retained;
}
