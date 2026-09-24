// Component event/element-tree tests with native-view and hook doubles.
// These cover JS behavior, not UIKit layout or an actual device render.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { runInNewContext } from 'node:vm';

import React from 'react';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const appRoot = fileURLToPath(new URL('../', import.meta.url));

function loadTS(filename, doubles = {}) {
  const { outputText } = ts.transpileModule(readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
    fileName: filename,
  });
  const exports = {};
  runInNewContext(outputText, {
    exports,
    Date,
    require(specifier) {
      if (specifier in doubles) return doubles[specifier];
      if (specifier.startsWith('.')) return loadTS(resolve(dirname(filename), `${specifier}.ts`), doubles);
      return require(specifier);
    },
  });
  return exports;
}

const dates = loadTS(resolve(appRoot, 'src/lib/dates.ts'));
const localDate = (value) => dates.parseDate(value);

function elements(node) {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!node || typeof node !== 'object' || !node.type) return [];
  return [node, ...elements(node.props.children)];
}

function field({ platform = 'ios', ...initialProps } = {}) {
  const states = [];
  let cursor = 0;
  let keyboardDismissals = 0;
  const changes = [];
  let props = {
    label: '目标日期', value: '', minimumDate: localDate('2026-09-16'), ...initialProps,
    onChange(value) { changes.push(value); props = { ...props, value }; },
  };
  const { DateField } = loadTS(resolve(appRoot, 'src/components/DateField.tsx'), {
    react: {
      __esModule: true, default: React, ...React,
      useState(initial) {
        const index = cursor++;
        if (states.length <= index) states[index] = typeof initial === 'function' ? initial() : initial;
        return [states[index], (value) => { states[index] = typeof value === 'function' ? value(states[index]) : value; }];
      },
    },
    'react-native': {
      Keyboard: { dismiss() { keyboardDismissals++; } }, Platform: { OS: platform },
      Modal: 'Modal', Pressable: 'Pressable', Text: 'Text', View: 'View',
      StyleSheet: { create: (styles) => styles, hairlineWidth: 0.5, absoluteFill: { position: 'absolute', top: 0, right: 0, bottom: 0, left: 0 } },
    },
    'react-native-safe-area-context': { SafeAreaView: 'SafeAreaView' },
    '@expo/vector-icons': { Ionicons: 'Icon' },
    '@expo/ui/community/datetime-picker': { __esModule: true, default: 'DateTimePicker' },
  });
  const render = () => { cursor = 0; return elements(DateField(props)); };
  const find = (type, predicate = () => true) => render().find((element) => element.type === type && predicate(element));
  const pressText = (text) => {
    const button = find('Pressable', (element) => elements(element.props.children).some((child) => child.type === 'Text' && child.props.children === text));
    assert.ok(button, `Missing button: ${text}`);
    button.props.onPress();
  };
  return {
    changes, render, find, pressText,
    open() { find('Pressable', (element) => element.props.accessibilityLabel === props.label).props.onPress(); },
    update(value) { props = { ...props, value }; },
    get value() { return props.value; },
    get keyboardDismissals() { return keyboardDismissals; },
  };
}

test('closed date field has no extra native date button below the input', () => {
  const fixture = field();
  assert.equal(fixture.find('Modal'), undefined);
  assert.equal(fixture.find('DateTimePicker'), undefined);
  assert.ok(fixture.find('Text', (element) => element.props.children === '请选择日期'));
});

test('iOS picker is inside a full-width modal, not a compact control in the form', () => {
  const fixture = field();
  fixture.open();
  assert.equal(fixture.keyboardDismissals, 1);
  const modal = fixture.find('Modal');
  assert.equal(modal.props.transparent, true);
  assert.ok(elements(modal).some((element) => element.type === 'DateTimePicker'));
  assert.equal(fixture.find('SafeAreaView').props.style.width, '100%');
  const picker = fixture.find('DateTimePicker');
  assert.equal(picker.props.style.width, '100%');
  assert.equal(picker.props.style.height, 216);
  assert.equal(picker.props.display, 'spinner');
  assert.equal(picker.props.presentation, undefined);
  assert.equal(picker.props.locale, 'zh_CN');
});

test('confirming the initial target date saves it even without a native change event', () => {
  const fixture = field();
  fixture.open();
  fixture.pressText('确定');
  assert.deepEqual(fixture.changes, ['2026-09-16']);
  assert.equal(fixture.find('Modal'), undefined);
  assert.ok(fixture.find('Text', (element) => element.props.children === '2026年9月16日'));
});

test('iOS wheel changes only the draft and keeps the modal open', () => {
  const fixture = field();
  fixture.open();
  fixture.find('DateTimePicker').props.onValueChange({}, localDate('2026-10-20'));
  assert.deepEqual(fixture.changes, []);
  assert.equal(fixture.value, '');
  assert.ok(fixture.find('Modal'));
  assert.ok(fixture.find('Text', (element) => element.props.children === '2026年10月20日'));
});

test('confirming an iOS wheel selection writes the date and closes the modal', () => {
  const fixture = field();
  fixture.open();
  fixture.find('DateTimePicker').props.onValueChange({}, localDate('2026-10-20'));
  fixture.pressText('确定');
  assert.deepEqual(fixture.changes, ['2026-10-20']);
  assert.equal(fixture.find('DateTimePicker'), undefined);
});

test('cancelling an empty target date never accepts its fallback', () => {
  const fixture = field();
  fixture.open();
  fixture.pressText('取消');
  assert.deepEqual(fixture.changes, []);
  assert.equal(fixture.value, '');
  assert.equal(fixture.find('Modal'), undefined);
});

test('cancelling a changed draft preserves the saved date and resets on reopen', () => {
  const fixture = field({ value: '2026-10-01' });
  fixture.open();
  fixture.find('DateTimePicker').props.onValueChange({}, localDate('2026-11-02'));
  fixture.pressText('取消');
  assert.equal(fixture.value, '2026-10-01');
  assert.deepEqual(fixture.changes, []);
  fixture.open();
  assert.equal(dates.dateText(fixture.find('DateTimePicker').props.value), '2026-10-01');
});

test('tapping outside the modal cancels without changing the field', () => {
  const fixture = field();
  fixture.open();
  fixture.find('Pressable', (element) => element.props.accessibilityLabel === '取消选择日期').props.onPress();
  assert.equal(fixture.find('Modal'), undefined);
  assert.deepEqual(fixture.changes, []);
});

test('native modal dismissal cancels without changing the field', () => {
  const fixture = field();
  fixture.open();
  fixture.find('Modal').props.onRequestClose();
  assert.equal(fixture.find('Modal'), undefined);
  assert.deepEqual(fixture.changes, []);
});

test('reopening uses the latest controlled value', () => {
  const fixture = field({ value: '2026-10-01' });
  fixture.open();
  fixture.pressText('取消');
  fixture.update('2026-12-01');
  fixture.open();
  assert.equal(dates.dateText(fixture.find('DateTimePicker').props.value), '2026-12-01');
});

test('measurement date fallback and upper bound prevent future dates', () => {
  const fixture = field({ label: '体重测量日期', value: '2026-10-01', minimumDate: undefined, maximumDate: localDate('2026-09-15') });
  fixture.open();
  assert.equal(dates.dateText(fixture.find('DateTimePicker').props.value), '2026-09-15');
  fixture.pressText('确定');
  assert.deepEqual(fixture.changes, ['2026-09-15']);
});

test('Android keeps its native dialog and dismisses without saving', () => {
  const fixture = field({ platform: 'android' });
  fixture.open();
  assert.equal(fixture.find('Modal'), undefined);
  const picker = fixture.find('DateTimePicker');
  assert.equal(picker.props.presentation, 'dialog');
  assert.equal(picker.props.positiveButton.label, '确定');
  assert.equal(picker.props.negativeButton.label, '取消');
  picker.props.onDismiss();
  assert.deepEqual(fixture.changes, []);
  assert.equal(fixture.find('DateTimePicker'), undefined);
});

test('Android native confirmation saves and unmounts the dialog', () => {
  const fixture = field({ platform: 'android' });
  fixture.open();
  fixture.find('DateTimePicker').props.onValueChange({}, localDate('2026-10-20'));
  assert.deepEqual(fixture.changes, ['2026-10-20']);
  assert.equal(fixture.find('DateTimePicker'), undefined);
});

test('picker initial dates use bounds for empty values and clamp existing values', () => {
  const minimum = localDate('2026-09-16');
  const maximum = localDate('2026-09-20');
  assert.equal(dates.dateText(dates.initialPickerDate('', minimum)), '2026-09-16');
  assert.equal(dates.dateText(dates.initialPickerDate('', undefined, maximum)), '2026-09-20');
  assert.equal(dates.dateText(dates.initialPickerDate('2026-09-01', minimum, maximum)), '2026-09-16');
  assert.equal(dates.dateText(dates.initialPickerDate('2026-10-01', minimum, maximum)), '2026-09-20');
  assert.equal(dates.dateText(dates.initialPickerDate('2026-09-18', minimum, maximum)), '2026-09-18');
  const draft = dates.initialPickerDate('', minimum);
  draft.setDate(30);
  assert.equal(dates.dateText(minimum), '2026-09-16');
});

test('date-only serialization preserves the local day across timezones', () => {
  const previous = process.env.TZ;
  try {
    for (const timezone of ['Asia/Shanghai', 'America/Los_Angeles', 'Pacific/Kiritimati']) {
      process.env.TZ = timezone;
      for (const value of ['2026-09-16', '2026-01-01', '2026-12-31', '2028-02-29']) {
        assert.equal(dates.dateText(localDate(value)), value, `${timezone}: ${value}`);
        assert.equal(localDate(value).getHours(), 12);
      }
    }
  } finally {
    if (previous === undefined) delete process.env.TZ;
    else process.env.TZ = previous;
  }
});
