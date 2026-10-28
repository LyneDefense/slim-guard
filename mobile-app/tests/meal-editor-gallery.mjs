// JS event tests with native view doubles; device layout is not simulated here.
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
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
  occurrence: { precision: 'date', on: '2026-10-28', at: null, timezone: 'Asia/Shanghai' },
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
  let states = [];
  let cursor = 0;
  let effects = [];
  const alerts = [];
  const hooks = {
    __esModule: true, ...React, default: React,
    useState(initial) {
      const index = cursor++;
      if (states.length <= index) states[index] = typeof initial === 'function' ? initial() : initial;
      const storage = states;
      return [storage[index], value => { storage[index] = typeof value === 'function' ? value(storage[index]) : value; }];
    },
    useRef(initial) { const index = cursor++; return states[index] ||= { current: initial }; },
    useMemo: factory => factory(),
    useCallback: callback => callback,
    useEffect(effect, dependencies) {
      const index = cursor++;
      if (!states[index] || dependencies.some((value, i) => value !== states[index][i])) effects.push(effect);
      states[index] = dependencies;
    },
  };
  const native = Object.fromEntries(['ActivityIndicator', 'Image', 'Modal', 'KeyboardAvoidingView', 'Pressable', 'ScrollView', 'Text', 'TextInput', 'View'].map(name => [name, name]));
  function load(filename) {
    const { outputText } = ts.transpileModule(readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } });
    const exports = {};
    runInNewContext(outputText, { exports, require(specifier) {
      if (specifier in doubles) return doubles[specifier];
      if (specifier === 'react') return hooks;
      if (specifier === 'react-native') return {
        ...native, Platform: { OS: 'ios' }, StyleSheet: { create: value => value, hairlineWidth: 0.5 },
        Alert: { alert: (...args) => alerts.push(args) },
        Animated: { View: 'AnimatedView', Value: class { constructor(value) { this.value = value; } setValue(value) { this.value = value; } }, spring: (value, config) => ({ start: () => value.setValue(config.toValue) }) },
        PanResponder: { create: handlers => ({ panHandlers: handlers }) },
      };
      if (specifier === '@expo/vector-icons') return { Ionicons: 'Icon' };
      if (specifier === 'expo-status-bar') return { StatusBar: 'StatusBar' };
      if (specifier === 'react-native-safe-area-context') return { SafeAreaView: 'SafeAreaView', SafeAreaProvider: 'SafeAreaProvider' };
      if (specifier.startsWith('.')) {
        const path = resolve(dirname(filename), specifier);
        return load(existsSync(`${path}.ts`) ? `${path}.ts` : `${path}.tsx`);
      }
      return require(specifier);
    } });
    return exports;
  }
  function mount(Component, componentProps) {
    const storage = [];
    const render = () => {
      states = storage; cursor = 0; effects = [];
      const nodes = tree(Component(componentProps));
      effects.forEach(effect => effect());
      return nodes;
    };
    const find = (type, predicate = () => true) => render().find(element => element.type === type && predicate(element.props));
    const press = label => find('Pressable', props => props.accessibilityLabel === label || tree(props.children).some(item => item.type === 'Text' && item.props.children === label)).props.onPress();
    return { render, find, press, alerts, mount: element => mount(element.type, element.props) };
  }
  return mount(load(resolve(root, file))[exportName], props);
}

function editor({ failFirst = false, pending = false } = {}) {
  const calls = [], saved = [], closed = [];
  let id = 0;
  const component = fixture('features/record-history/MealEditor.tsx', 'MealEditor', { record, onClose: () => closed.push(true), onSaved: value => saved.push(value) }, {
    'expo-crypto': { randomUUID: () => `uuid-${++id}` },
    './api': { recordHistoryApi: { async editMeal(recordId, input) {
      calls.push({ recordId, input });
      if (pending) return new Promise(() => {});
      if (failFirst && calls.length === 1) throw new Error('网络超时');
      return { ...record, content: { ...record.content, ...input } };
    } } },
  });
  return { ...component, calls, saved, closed };
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
  assert.equal(view.render().some(element => typeof element.type === 'function' && element.type.name === 'PhotoViewer'), false);
  view.press('查看第 1 张餐食照片');
  const viewer = view.mount(view.render().find(element => typeof element.type === 'function' && element.type.name === 'PhotoViewer'));
  viewer.find('View', props => !!props.onLayout).props.onLayout({ nativeEvent: { layout: { width: 390, height: 640 } } });
  const photo = viewer.mount(viewer.render().find(element => typeof element.type === 'function' && element.type.name === 'ZoomablePhoto'));
  const fullImage = photo.find('Image', props => props.resizeMode === 'contain');
  assert.equal(fullImage.props.source.headers.Authorization, 'Bearer test');
  assert.equal(fullImage.props.style.width, 390);
  assert.equal(fullImage.props.style.height, 640);
  viewer.press('关闭大图');
  assert.equal(view.render().some(element => typeof element.type === 'function' && element.type.name === 'PhotoViewer'), false);
});

test('editor uses a modal-local safe area and keeps large action buttons outside its scroll body', () => {
  const view = editor();
  assert.equal(view.find('Modal').props.transparent, true);
  assert.ok(view.find('SafeAreaProvider'));
  const sheet = view.find('View', props => props.accessibilityViewIsModal);
  assert.equal(sheet.props.style.maxHeight, '85%');
  const footer = view.find('SafeAreaView', props => props.edges?.includes('bottom'));
  assert.ok(tree(footer.props.children).some(item => item.props.accessibilityLabel === '保存餐食修改'));
  assert.equal(tree(view.find('ScrollView').props.children).some(item => item.props.accessibilityLabel === '保存餐食修改'), false);
  for (const label of ['保存餐食修改', '取消餐食修改']) {
    assert.ok(view.find('Pressable', props => props.accessibilityLabel === label).props.style.some(style => style?.minHeight >= 44));
  }
  assert.equal(view.find('KeyboardAvoidingView').props.behavior, 'padding');
});

test('cancel, backdrop, and system back confirm discarding edits; an untouched form closes directly', () => {
  const untouched = editor();
  untouched.press('取消餐食修改');
  assert.equal(untouched.closed.length, 1);
  assert.equal(untouched.alerts.length, 0);
  for (const method of ['取消餐食修改', '关闭餐食编辑', 'systemBack']) {
    const view = editor();
    view.find('TextInput', props => props.accessibilityLabel === '第 1 道菜的名称').props.onChangeText('炒鸡肉');
    if (method === 'systemBack') view.find('Modal').props.onRequestClose();
    else view.press(method);
    assert.equal(view.closed.length, 0);
    assert.equal(view.alerts.length, 1);
    view.alerts[0][2].find(button => button.text === '放弃修改').onPress();
    assert.equal(view.closed.length, 1);
  }
});

test('saving blocks every dismiss path to avoid abandoning an in-flight edit', () => {
  const view = editor({ pending: true });
  view.press('保存餐食修改');
  assert.equal(view.find('Pressable', props => props.accessibilityLabel === '取消餐食修改').props.disabled, true);
  assert.equal(view.find('Pressable', props => props.accessibilityLabel === '关闭餐食编辑').props.disabled, true);
  view.find('Modal').props.onRequestClose();
  assert.equal(view.closed.length, 0);
});

test('history uses an accessible pencil icon with a 44-point touch area, not a text-only edit link', async () => {
  const view = fixture('features/record-history/RecordHistory.tsx', 'RecordHistory', {
    timezone: 'Asia/Shanghai', refreshKey: 0, onChanged() {},
  }, {
    '../../components/ui': { Card: 'Card', SectionTitle: 'SectionTitle' },
    '../../components/PhotoGallery': { PhotoGallery: 'PhotoGallery' },
    './MealEditor': { MealEditor: 'MealEditor' },
    './api': { recordHistoryApi: { list: async () => ({ records: [record], has_more: false }) } },
  });
  const section = view.mount(view.render().find(node => typeof node.type === 'function' && node.type.name === 'RecordSection' && node.props.kind === 'meal'));
  section.press('饮食记录');
  section.render();
  await new Promise(setImmediate);
  const row = section.mount(section.render().find(node => typeof node.type === 'function' && node.type.name === 'RecordRow'));
  const button = row.find('Pressable', props => props.accessibilityLabel === '修改这条餐食记录');
  assert.equal(button.props.style.width, 44);
  assert.equal(button.props.style.height, 44);
  assert.equal(button.props.children.type, 'Icon');
  assert.equal(button.props.children.props.name, 'pencil-outline');
});

function photoViewer(initialIndex = 0) {
  const closed = [];
  const viewer = fixture('components/PhotoViewer.tsx', 'PhotoViewer', {
    photos: [{ uri: 'file:///one.jpg' }, { uri: 'file:///two.jpg' }], initialIndex, onClose: () => closed.push(true),
  }, { '../lib/api': { mobileApi: { imageSource: photo => ({ uri: photo.uri }) } } });
  const measure = (width, height) => viewer.find('View', props => !!props.onLayout).props.onLayout({ nativeEvent: { layout: { width, height } } });
  measure(390, 640);
  const pages = () => viewer.render().filter(element => typeof element.type === 'function' && element.type.name === 'ZoomablePhoto');
  return { viewer, closed, measure, pages };
}

test('photo viewer measures its available viewport, places close in the footer and supports rotation', () => {
  const { viewer, pages, measure } = photoViewer(1);
  assert.ok(viewer.find('SafeAreaProvider'));
  assert.equal(viewer.find('ScrollView', props => props.horizontal).props.contentOffset.x, 390);
  assert.equal(pages()[1].props.height, 640);
  const safe = viewer.find('SafeAreaView');
  const footer = safe.props.children[1];
  assert.equal(footer.type, 'View');
  assert.ok(tree(footer).some(item => item.props.accessibilityLabel === '关闭大图'));
  assert.equal(viewer.find('Pressable', props => props.accessibilityLabel === '关闭大图').props.style.minHeight, 48);
  measure(800, 290);
  assert.equal(pages()[1].props.width, 800);
  assert.equal(pages()[1].props.height, 290);
  assert.equal(viewer.find('ScrollView', props => props.horizontal).props.contentOffset.x, 800);
});

test('downward swipe closes only an unzoomed single-touch image; short and sideways drags do not', () => {
  const { viewer, pages, closed } = photoViewer();
  const photo = viewer.mount(pages()[0]);
  const handlers = photo.find('AnimatedView').props;
  const event = {};
  const single = { numberActiveTouches: 1, dx: 0, dy: 100, vy: 0.2 };
  handlers.onStartShouldSetPanResponderCapture(event, single);
  assert.equal(handlers.onMoveShouldSetPanResponderCapture(event, single), true);
  assert.equal(handlers.onMoveShouldSetPanResponderCapture(event, { ...single, dx: 140 }), false);
  handlers.onPanResponderRelease(event, { ...single, dy: 20 });
  assert.equal(closed.length, 0);
  handlers.onPanResponderRelease(event, single);
  assert.equal(closed.length, 1);
});

test('pinching/zooming cannot dismiss the viewer or switch photos while zoomed', () => {
  const { viewer, pages, closed } = photoViewer();
  const photo = viewer.mount(pages()[0]);
  const handlers = photo.find('AnimatedView').props;
  const single = { numberActiveTouches: 1, dx: 0, dy: 160, vy: 1 };
  handlers.onStartShouldSetPanResponderCapture({}, { ...single, numberActiveTouches: 2 });
  assert.equal(handlers.onMoveShouldSetPanResponderCapture({}, single), false);
  handlers.onPanResponderRelease({}, single);
  assert.equal(closed.length, 0);
  photo.find('ScrollView').props.onScroll({ nativeEvent: { zoomScale: 2 } });
  assert.equal(viewer.find('ScrollView', props => props.horizontal).props.scrollEnabled, false);
  handlers.onStartShouldSetPanResponderCapture({}, single);
  assert.equal(handlers.onMoveShouldSetPanResponderCapture({}, single), false);
  handlers.onPanResponderRelease({}, single);
  assert.equal(closed.length, 0);
  photo.find('ScrollView').props.onScroll({ nativeEvent: { zoomScale: 1 } });
  assert.equal(viewer.find('ScrollView', props => props.horizontal).props.scrollEnabled, true);
  assert.equal(handlers.onMoveShouldSetPanResponderCapture({}, single), true);
});
