// Brand configuration and JS lifecycle tests; not native launch-screen acceptance.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { runInNewContext } from 'node:vm';
import React from 'react';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const root = fileURLToPath(new URL('../', import.meta.url));
const config = JSON.parse(readFileSync(resolve(root, 'app.json'), 'utf8')).expo;
const tick = () => new Promise(setImmediate);

function tree(node) {
  if (Array.isArray(node)) return node.flatMap(tree);
  if (!node || typeof node !== 'object' || !node.type) return [];
  return [node, ...tree(node.props.children)];
}

function fixture(file, exportName, props = {}, { reduced = Promise.resolve(false), doubles = {} } = {}) {
  const states = [], cache = new Map(), animations = [], nativeEvents = new Map(), splashEvents = [];
  let cursor = 0, effects = [];
  const hooks = {
    __esModule: true, ...React, default: React,
    useState(initial) {
      const index = cursor++;
      if (!(index in states)) states[index] = typeof initial === 'function' ? initial() : initial;
      return [states[index], value => { states[index] = typeof value === 'function' ? value(states[index]) : value; }];
    },
    useRef(initial) { const index = cursor++; return states[index] ||= { current: initial }; },
    useCallback: callback => callback,
    useEffect(effect, deps) {
      const index = cursor++, previous = states[index];
      if (!previous || deps.some((value, i) => value !== previous.deps[i])) {
        effects.push(() => { previous?.cleanup?.(); states[index] = { effectSlot: true, deps, cleanup: effect() }; });
      }
    },
  };
  const native = {
    ...Object.fromEntries(['ActivityIndicator', 'Image', 'View', 'Text'].map(name => [name, name])),
    StyleSheet: { create: value => value, hairlineWidth: 0.5 },
    Platform: { OS: 'ios' },
    AccessibilityInfo: {
      isReduceMotionEnabled: () => reduced,
      addEventListener(event, handler) {
        nativeEvents.set(event, handler);
        return { remove: () => nativeEvents.delete(event) };
      },
    },
    Animated: {
      Image: 'AnimatedImage',
      Value: class { constructor(value) { this.value = value; } setValue(value) { this.value = value; } },
      timing: (value, options) => ({ value, options }),
      sequence: items => items,
      loop(items) {
        const pulse = { items, started: false, stopped: false, start() { this.started = true; }, stop() { this.stopped = true; } };
        animations.push(pulse);
        return pulse;
      },
    },
  };

  function load(filename) {
    if (filename.endsWith('.png')) return filename;
    if (filename.endsWith('.json')) return JSON.parse(readFileSync(filename, 'utf8'));
    if (cache.has(filename)) return cache.get(filename);
    const { outputText } = ts.transpileModule(readFileSync(filename, 'utf8'), { compilerOptions: {
      module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true, target: ts.ScriptTarget.ES2022,
    } });
    const exports = {};
    cache.set(filename, exports);
    runInNewContext(outputText, { exports, require(specifier) {
      if (specifier in doubles) return doubles[specifier];
      if (specifier === 'react') return hooks;
      if (specifier === 'react-native') return native;
      if (specifier === '@expo/vector-icons') return { Ionicons: 'Icon' };
      if (specifier === 'react-native-svg') return { __esModule: true, default: 'Svg', Circle: 'Circle', Path: 'Path' };
      if (specifier === 'expo-status-bar') return { StatusBar: 'StatusBar' };
      if (specifier === 'react-native-safe-area-context') return { SafeAreaView: 'SafeAreaView', SafeAreaProvider: 'SafeAreaProvider' };
      if (specifier === 'expo-splash-screen') return {
        preventAutoHideAsync: async () => { splashEvents.push('prevent'); },
        hideAsync: async () => { splashEvents.push('hide'); },
      };
      if (specifier.endsWith('/context/AppContext')) return { AppProvider: 'AppProvider' };
      if (specifier.includes('/screens/')) return { [specifier.split('/').at(-1)]: 'Screen' };
      if (specifier.startsWith('.')) {
        const path = resolve(dirname(filename), specifier);
        if (/\.(json|png)$/.test(path)) return load(path);
        try { return load(`${path}.ts`); } catch (error) {
          if (error.code !== 'ENOENT') throw error;
          return load(`${path}.tsx`);
        }
      }
      return require(specifier);
    } });
    return exports;
  }
  const Component = load(resolve(root, file))[exportName];
  return {
    animations, nativeEvents, splashEvents,
    render() { cursor = 0; effects = []; const nodes = tree(Component(props)); effects.forEach(effect => effect()); return nodes; },
    unmount() { states.filter(value => value?.effectSlot).forEach(value => value.cleanup?.()); },
    load,
  };
}

test('display rename retains native identities and all configured assets are valid local PNGs', () => {
  assert.equal(config.name, '称小二');
  assert.equal(config.slug, 'slim-guard');
  assert.equal(config.scheme, 'slimguard');
  assert.equal(config.ios.bundleIdentifier, 'com.slimguard.app');
  assert.equal(config.android.package, 'com.slimguard.app');
  const splash = config.plugins.find(plugin => Array.isArray(plugin) && plugin[0] === 'expo-splash-screen')[1];
  assert.equal(splash.backgroundColor, config.android.adaptiveIcon.backgroundColor);
  assert.equal(splash.image, config.android.adaptiveIcon.foregroundImage);
  for (const path of [config.icon, config.web.favicon, splash.image]) {
    const png = readFileSync(resolve(root, path));
    assert.equal(png.subarray(1, 4).toString(), 'PNG');
    assert.equal(png.readUInt32BE(16), png.readUInt32BE(20));
    assert.ok(png.readUInt32BE(16) >= 1024);
  }
  assert.equal(readFileSync(resolve(root, splash.image))[25], 6, 'transparent foreground uses an RGBA PNG');
  for (const prompt of Object.values(config.plugins.find(plugin => Array.isArray(plugin) && plugin[0] === 'expo-image-picker')[1]).filter(value => typeof value === 'string')) {
    assert.ok(prompt.includes(config.name));
    assert.equal(prompt.includes('SlimGuard'), false);
  }
});

test('the launcher icon is the exact selected artwork, not a regenerated approximation', () => {
  const provenance = JSON.parse(readFileSync(resolve(root, 'assets/brand/provenance.json'), 'utf8'));
  const original = resolve(root, 'assets/brand', provenance.selected_icon.source);
  const digest = path => createHash('sha256').update(readFileSync(path)).digest('hex');
  assert.equal(digest(original), digest(resolve(root, config.icon)));
  const prompts = JSON.parse(readFileSync(resolve(root, 'assets/brand', provenance.selected_icon.prompt_source), 'utf8'));
  assert.equal(prompts.concepts.find(concept => concept.id === 'A').asset, 'logo-a-apricot-scale.png');
});

test('header uses the selected icon and centralized Chinese name, including compact layout', () => {
  const view = fixture('src/shared/brand/BrandMark.tsx', 'BrandMark', { compact: true });
  const nodes = view.render();
  assert.ok(nodes.some(node => node.type === 'Text' && node.props.children === config.name));
  assert.equal(nodes.find(node => typeof node.type === 'function' && node.type.name === 'BrandIcon').props.size, 36);
  const icon = fixture('src/shared/brand/BrandMark.tsx', 'BrandIcon', { size: 36 }).render().find(node => node.type === 'Image');
  assert.equal(icon.props.source, resolve(root, config.icon));
  assert.equal(icon.props.accessible, false, 'decorative icon does not repeat the nearby accessible name');
});

test('both loading text colors retain accessible contrast against the apricot background', () => {
  const view = fixture('src/shared/brand/BrandMark.tsx', 'BrandMark');
  const { brand } = view.load(resolve(root, 'src/shared/brand/identity.ts'));
  const luminance = color => color.slice(1).match(/../g)
    .map(value => parseInt(value, 16) / 255)
    .map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4)
    .reduce((sum, value, i) => sum + value * [0.2126, 0.7152, 0.0722][i], 0);
  for (const text of [brand.colors.ink, brand.colors.inkMuted]) {
    assert.ok((luminance(brand.colors.background) + 0.05) / (luminance(text) + 0.05) >= 4.5);
  }
});

test('loading uses the native splash palette and only starts breathing after motion preference is known', async () => {
  const view = fixture('src/shared/brand/LoadingScreen.tsx', 'LoadingScreen');
  const first = view.render();
  const splash = config.plugins.find(plugin => Array.isArray(plugin) && plugin[0] === 'expo-splash-screen')[1];
  assert.equal(first.find(node => node.type === 'SafeAreaView').props.style.backgroundColor, splash.backgroundColor);
  assert.equal(first.find(node => node.type === 'AnimatedImage').props.source, resolve(root, splash.image));
  assert.ok(first.some(node => node.props.accessibilityLabel === `正在加载${config.name}`));
  assert.equal(view.animations.length, 0);
  await tick(); view.render();
  const pulse = view.animations[0];
  assert.equal(pulse.started, true);
  assert.ok(pulse.items.every(item => item.options.useNativeDriver && item.options.isInteraction === false));
  view.nativeEvents.get('reduceMotionChanged')(true); view.render();
  assert.equal(pulse.stopped, true);
  assert.equal(pulse.items[0].value.value, 1);
  assert.equal(view.render().find(node => node.type === 'ActivityIndicator').props.animating, false);
  view.unmount();
  assert.equal(view.nativeEvents.size, 0);
});

test('a newer motion preference cannot be overwritten by an older asynchronous query', async () => {
  let resolvePreference;
  const reduced = new Promise(resolve => { resolvePreference = resolve; });
  const view = fixture('src/shared/brand/LoadingScreen.tsx', 'LoadingScreen', {}, { reduced });
  view.render();
  view.nativeEvents.get('reduceMotionChanged')(true);
  resolvePreference(false); await tick(); view.render();
  assert.equal(view.animations.length, 0);
  view.unmount();
});

test('unmount stops the breathing loop and removes the native subscription', async () => {
  const view = fixture('src/shared/brand/LoadingScreen.tsx', 'LoadingScreen');
  view.render(); await tick(); view.render();
  assert.equal(view.animations[0].started, true);
  view.unmount();
  assert.equal(view.animations[0].stopped, true);
  assert.equal(view.nativeEvents.size, 0);
});

test('native splash is released on first root layout, not held for an artificial delay or API result', () => {
  const view = fixture('App.tsx', 'default');
  assert.equal(view.splashEvents.join(','), 'prevent');
  const rootView = view.render().find(node => node.type === 'View' && node.props.onLayout);
  assert.equal(view.splashEvents.join(','), 'prevent');
  rootView.props.onLayout();
  assert.equal(view.splashEvents.join(','), 'prevent,hide');
});

test('scheduled meal reminder uses the new display name without changing existing reminder identifiers', async () => {
  const scheduled = [], cancelled = [];
  const view = fixture('src/shared/brand/BrandMark.tsx', 'BrandMark', {}, { doubles: {
    'expo-notifications': {
      setNotificationHandler() {},
      cancelScheduledNotificationAsync: async id => { cancelled.push(id); },
      getPermissionsAsync: async () => ({ status: 'granted' }),
      scheduleNotificationAsync: async value => { scheduled.push(value); },
      SchedulableTriggerInputTypes: { DAILY: 'daily' },
    },
    'expo-constants': {}, 'expo-crypto': {}, 'expo-device': { isDevice: false },
    './api': { mobileApi: {} }, './session': {},
  } });
  const notifications = view.load(resolve(root, 'src/lib/notifications.ts'));
  assert.equal(await notifications.syncRoutineNotifications({ weight_reminder_time: null, meal_reminder_time: '12:30', daily_review_time: null }), true);
  assert.equal(cancelled.length, 3);
  assert.equal(scheduled.length, 1);
  assert.equal(scheduled[0].identifier, 'slimguard-routine-meal');
  assert.ok(scheduled[0].content.body.includes(config.name));
  assert.equal(scheduled[0].content.body.includes('SlimGuard'), false);
});
