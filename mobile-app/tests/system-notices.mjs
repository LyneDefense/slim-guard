import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';

const filename = fileURLToPath(new URL('../src/features/system-notices/noticeFeed.ts', import.meta.url));
const { outputText } = ts.transpileModule(readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } });
const exports = {};
runInNewContext(outputText, { exports });
const { systemNoticeCandidates, mergeNotices } = exports;
const now = Date.parse('2026-10-28T08:00:00Z');
const message = (id, extra = {}) => ({ id, turn_id: 'turn1', participant: 'system_assistant', role: 'assistant', kind: 'text', text: '已经记录三道菜。', notice_text: '早餐已记录。', created_at: new Date(now).toISOString(), ...extra });

test('system text and its card produce one concise notice, never a second chat message', () => {
  const candidates = systemNoticeCandidates([
    message('text'), message('card', { kind: 'card', text: null, card: { card_type: 'meal_record' } }),
    message('coach', { participant: 'coach', text: '搭配不错。' }),
  ]);
  assert.equal(candidates.length, 1);
  assert.equal(candidates[0].text, '早餐已记录。');
});

test('old history does not replay transient notices while unanswered questions survive reload', () => {
  const candidates = systemNoticeCandidates([
    message('old', { turn_id: 'old', created_at: new Date(now - 60_000).toISOString() }),
    message('question', { requires_response: true, notice_text: '这道菜叫什么？' }),
  ]);
  const result = mergeNotices([], candidates, new Set(), now, true);
  assert.equal(result.length, 1);
  assert.equal(result[0].requiresResponse, true);
  assert.equal(result[0].expiresAt, null);
});

test('transient notices expire at ten seconds and refreshing cannot replay a dismissed notice', () => {
  const candidates = systemNoticeCandidates([message('result')]);
  const seen = new Set();
  const first = mergeNotices([], candidates, seen, now, false);
  assert.equal(first[0].expiresAt, now + 10_000);
  assert.equal(seen.size, 0, 'the reducer must be pure under React Strict Mode');
  seen.add(first[0].id);
  assert.equal(mergeNotices(first, candidates, seen, now + 10_000, false).length, 0);
  assert.equal(mergeNotices([], candidates, seen, now + 500, false).length, 0);
});

test('a new user answer removes the old pending question; a further question remains reachable', () => {
  const question = message('question', { requires_response: true });
  const first = mergeNotices([], systemNoticeCandidates([question]), new Set(), now, false);
  const messages = [question, message('user', { participant: 'user', role: 'user', turn_id: 'turn2', text: '香菇' })];
  assert.equal(mergeNotices(first, systemNoticeCandidates(messages), new Set([first[0].id]), now, false).length, 0);
  messages.push(message('followup', { turn_id: 'turn2', requires_response: true }));
  assert.equal(systemNoticeCandidates(messages).filter(item => item.requiresResponse).length, 1);
});
