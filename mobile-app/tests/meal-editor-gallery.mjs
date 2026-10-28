// JS event tests with native view doubles; device layout is not simulated here.
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
const root = fileURLToPath(new URL('../src/', import.meta.url));
const record = {
  ref: { kind: 'meal', record_id: 'meal1', revision: 4 },
  content: { kind: 'meal', meal_type: 'breakfast', photo_asset_ids: ['photo1'], foods: [
    { item_id: 'f1', name: '鸡肉炒菜', portion: '一份' }, { item_id: 'f2', name: '花菜', portion: null },
  ] },
};

function tree(node) {
  if (Array.isArray(node)) return node.flatMap(tree);
  if (!node || typeof node !== 'object' || !node.type) return [];
  return [node, ...tree(node.props.children)];
}

function fixture(file, exportName, props, doubles) {
  const states = [];
  let cursor = 0;
  const hooks = {
    __esModule: true, ...React, default: React,
    useState(initial) {
      const index = cursor++;
      if (states.length <= index) states[index] = typeof initial === 'function' ? initial() : initial;
      return [states[index], value => { states[index] = typeof value === 'function' ? value(states[index]) : value; }];
    },
    useRef(initial) { const index = cursor++; return states[index] ||= { current: initial }; },
  };
  const native = Object.fromEntries(['ActivityIndicator', 'Image', 'Modal', 'KeyboardAvoidingView', 'Pressable', 'ScrollView', 'Text', 'TextInput', 'View'].map(name => [name, name]));
  function load(filename) {
    const { outputText } = ts.transpileModule(readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } });
    const exports = {};
    runInNewContext(outputText, { exports, require(specifier) {
      if (specifier in doubles) return doubles[specifier];
      if (specifier === 'react') return hooks;
      if (specifier === 'react-native') return { ...native, Platform: { OS: 'ios' }, StyleSheet: { create: value => value, hairlineWidth: 0.5 }, useWindowDimensions: () => ({ width: 390, height: 844 }) };
      if (specifier === '@expo/vector-icons') return { Ionicons: 'Icon' };
      if (specifier === 'react-native-safe-area-context') return { SafeAreaView: 'SafeAreaView' };
      if (specifier.startsWith('.')) return load(resolve(dirname(filename), `${specifier}.ts`));
      return require(specifier);
    } });
    return exports;
  }
  const Component = load(resolve(root, file))[exportName];
  const render = () => { cursor = 0; return tree(Component(props)); };
  const find = (type, predicate) => render().find(element => element.type === type && predicate(element.props));
  const press = label => find('Pressable', props => props.accessibilityLabel === label || tree(props.children).some(item => item.type === 'Text' && item.props.children === label)).props.onPress();
  return { render, find, press };
}

function editor({ failFirst = false } = {}) {
  const calls = [], saved = [];
  let id = 0;
  const component = fixture('features/record-history/MealEditor.tsx', 'MealEditor', { record, onClose() {}, onSaved: value => saved.push(value) }, {
    'expo-crypto': { randomUUID: () => `uuid-${++id}` },
    './api': { recordHistoryApi: { async editMeal(recordId, input) {
      calls.push({ recordId, input });
      if (failFirst && calls.length === 1) throw new Error('网络超时');
      return { ...record, content: { ...record.content, ...input } };
    } } },
  });
  return { ...component, calls, saved };
}

test('editor changes names/meal type and adds/removes dishes through direct record API', async () => {
  const view = editor();
  view.find('TextInput', props => props.accessibilityLabel === '第 1 道菜的名称').props.onChangeText('香菇炒鸡肉');
  view.press('删除第 2 道菜');
  view.press('添加菜品');
  assert.equal(view.find('Pressable', props => props.accessibilityLabel === '保存餐食修改').props.disabled, true);
  view.find('TextInput', props => props.accessibilityLabel === '第 2 道菜的名称').props.onChangeText('米饭');
  view.press('晚餐');
  view.press('保存餐食修改');
  await new Promise(setImmediate);
  assert.equal(view.calls.length, 1);
  assert.equal(view.calls[0].recordId, 'meal1');
  assert.equal(view.calls[0].input.expected_revision, 4);
  assert.equal(view.calls[0].input.meal_type, 'dinner');
  assert.deepEqual(JSON.parse(JSON.stringify(view.calls[0].input.foods)), [{ item_id: 'f1', name: '香菇炒鸡肉' }, { item_id: null, name: '米饭' }]);
  assert.equal(view.saved.length, 1);
});

test('retrying an unchanged edit reuses its operation ID after an uncertain network result', async () => {
  const view = editor({ failFirst: true });
  view.press('保存餐食修改');
  await new Promise(setImmediate);
  assert.equal(view.saved.length, 0);
  view.press('保存餐食修改');
  await new Promise(setImmediate);
  assert.equal(view.calls.length, 2);
  assert.equal(view.calls[0].input.operation_id, view.calls[1].input.operation_id);
  assert.equal(view.saved.length, 1);
});

test('meal thumbnail opens its authenticated full-size image and can be closed', () => {
  const view = fixture('components/PhotoGallery.tsx', 'PhotoGallery', {
    compact: true, photos: [{ asset_id: 'photo1', mime_type: 'image/png' }],
  }, { '../lib/api': { mobileApi: { imageSource: photo => ({ uri: `/assets/${photo.asset_id}`, headers: { Authorization: 'Bearer test' } }) } } });
  assert.equal(view.find('Modal', () => true).props.visible, false);
  view.press('查看第 1 张餐食照片');
  assert.equal(view.find('Modal', () => true).props.visible, true);
  const fullImage = view.find('Image', props => props.resizeMode === 'contain');
  assert.equal(fullImage.props.source.headers.Authorization, 'Bearer test');
  assert.equal(fullImage.props.style.width, 390);
  view.press('关闭大图');
  assert.equal(view.find('Modal', () => true).props.visible, false);
});
