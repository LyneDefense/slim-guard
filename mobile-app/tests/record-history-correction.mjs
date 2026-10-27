import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { runInNewContext } from 'node:vm';

import ts from 'typescript';

const featureRoot = fileURLToPath(new URL('../src/features/record-history/', import.meta.url));

function loadCorrectionDraft() {
  const filename = resolve(featureRoot, 'correctionDraft.ts');
  const { outputText } = ts.transpileModule(readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    fileName: filename,
  });
  const exports = {};
  runInNewContext(outputText, { exports });
  return exports.mealCorrectionDraft;
}

test('correction entry starts with the selected meal context but not recognition nag text', () => {
  const mealCorrectionDraft = loadCorrectionDraft();
  const draft = mealCorrectionDraft({
    ref: { kind: 'meal', record_id: 'meal-1', revision: 2 },
    occurrence: { precision: 'date', at: null, on: '2026-10-27', timezone: 'Asia/Shanghai' },
    content: {
      kind: 'meal',
      meal_type: 'breakfast',
      foods: [
        { item_id: 'food-1', name: '辣椒炒猪肝', portion: null },
        { item_id: 'food-2', name: '白米饭', portion: '一小碗' },
      ],
    },
    recognition_issues: [{ issue_id: 'issue-1', user_message: '如果有误请帮我更正一下' }],
  }, 'Asia/Shanghai');

  assert.match(draft, /2026-10-27/);
  assert.match(draft, /早餐/);
  assert.match(draft, /辣椒炒猪肝、白米饭（一小碗）/);
  assert.match(draft, /请帮我更正为：$/);
  assert.doesNotMatch(draft, /如果有误请帮我更正一下/);
});

test('correction draft is only available for meal records', () => {
  const mealCorrectionDraft = loadCorrectionDraft();
  const exercise = {
    ref: { kind: 'exercise', record_id: 'exercise-1', revision: 1 },
    occurrence: { precision: 'unknown', at: null, on: null, timezone: 'UTC' },
    content: { kind: 'exercise', activity_name: '散步', duration_minutes: 20, steps: null },
    recognition_issues: [],
  };
  assert.equal(mealCorrectionDraft(exercise, 'UTC'), undefined);
});

test('record history renders an explicit correction action instead of recognition prompts', () => {
  const source = readFileSync(resolve(featureRoot, 'RecordHistory.tsx'), 'utf8');
  assert.match(source, /accessibilityLabel="更正这条饮食记录"/);
  assert.match(source, /mealCorrectionDraft\(record, timezone\)/);
  assert.doesNotMatch(source, /issue\.user_message/);
});
