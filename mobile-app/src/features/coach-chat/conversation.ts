import type { ChatMessage } from '../../types';

function dateParts(value: Date, timezone: string) {
  const parts = new Intl.DateTimeFormat('en-GB', { timeZone: timezone, year: 'numeric', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(value);
  const part = (type: string) => parts.find(item => item.type === type)?.value || '';
  return { year: part('year'), month: part('month'), day: part('day'), time: `${part('hour')}:${part('minute')}` };
}

export function chatTimeLabel(value: string, timezone: string, now = new Date()): string | null {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return null;
  const stamp = dateParts(date, timezone), today = dateParts(now, timezone);
  const sameYear = stamp.year === today.year;
  const sameDay = sameYear && stamp.month === today.month && stamp.day === today.day;
  return `${sameDay ? '今天' : `${sameYear ? '' : `${stamp.year}年`}${Number(stamp.month)}月${Number(stamp.day)}日`} ${stamp.time}`;
}

export function conversationItems(messages: ChatMessage[], timezone: string, now = new Date()) {
  let previousTime: number | null = null;
  let previousDay: string | null = null;
  return messages.filter(message => message.participant !== 'system_assistant' && message.kind !== 'card'
    && (message.text?.trim() || message.attachments?.length || message.pending || message.failed || message.kind === 'image'))
    .map(message => {
      const timestamp = Date.parse(message.created_at);
      const date = Number.isFinite(timestamp) ? dateParts(new Date(timestamp), timezone) : null;
      const day = date ? `${date.year}-${date.month}-${date.day}` : null;
      const showTime = date !== null && (previousTime === null || day !== previousDay || Math.abs(timestamp - previousTime) >= 5 * 60_000);
      if (showTime) { previousTime = timestamp; previousDay = day; }
      return { message, timeLabel: showTime ? chatTimeLabel(message.created_at, timezone, now) : null };
    });
}
