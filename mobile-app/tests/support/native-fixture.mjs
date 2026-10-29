// JS hook/event fixture, deliberately not a native layout renderer.
import { existsSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { runInNewContext } from 'node:vm';
import React from 'react';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const root = fileURLToPath(new URL('../../src/', import.meta.url));
export const tick = () => new Promise(setImmediate);
export function tree(node) {
  if (Array.isArray(node)) return node.flatMap(tree);
  if (!node || typeof node !== 'object' || !node.type) return [];
  return [node, ...tree(node.props.children)];
}
export function fixture(file, name, initialProps = {}, doubles = {}) {
  const cache = new Map();
  let storage, cursor, effects;
  const hooks = {
    __esModule: true, ...React, default: React,
    useState(initial) {
      const index = cursor++, state = storage;
      if (!(index in state)) state[index] = typeof initial === 'function' ? initial() : initial;
      return [state[index], value => { state[index] = typeof value === 'function' ? value(state[index]) : value; }];
    },
    useRef(initial) { const index = cursor++; return storage[index] ||= { current: initial }; },
    useId: () => `test-${cursor++}`,
    useMemo: factory => factory(), useCallback: callback => callback,
    useEffect(effect, deps) {
      const index = cursor++, state = storage, previous = state[index];
      if (!previous || !deps || deps.some((value, i) => value !== previous.deps[i])) {
        effects.push(() => { previous?.cleanup?.(); state[index] = { deps, cleanup: effect(), effect: true }; });
      }
    },
  };
  function load(filename) {
    if (filename.endsWith('.json')) return JSON.parse(readFileSync(filename, 'utf8'));
    if (filename.endsWith('.png')) return filename;
    if (cache.has(filename)) return cache.get(filename);
    const exports = {}; cache.set(filename, exports);
    const { outputText } = ts.transpileModule(readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } });
    runInNewContext(outputText, { exports, Intl, URLSearchParams, setTimeout, clearTimeout, setInterval, clearInterval, ...doubles.$globals, require(specifier) {
      if (specifier in doubles) return doubles[specifier];
      if (specifier === 'react') return hooks;
      if (specifier === 'react-native') return {
        ...Object.fromEntries(['ActivityIndicator','Image','Modal','Pressable','RefreshControl','ScrollView','Switch','Text','TextInput','View','KeyboardAvoidingView'].map(name => [name,name])),
        Platform: { OS: 'ios' },
        AppState: { currentState: 'active', addEventListener: (event, handler) => doubles.$appState?.(event, handler) || { remove() {} } },
        Keyboard: { dismiss: () => doubles.$dismissKeyboard?.(), addListener: (event, handler) => doubles.$keyboard?.(event, handler) || { remove() {} } },
        BackHandler: { addEventListener: (event, handler) => doubles.$backHandler?.(event, handler) || { remove() {} } },
        Alert: { alert: (...args) => doubles.$alert?.(...args) },
        StyleSheet: { create: value => value, hairlineWidth: 0.5, absoluteFill: { position: 'absolute' } },
      };
      if (specifier === 'react-native-svg') return { __esModule: true, default: 'Svg', ...Object.fromEntries(['Circle','Path','Defs','LinearGradient','Rect','Stop'].map(name => [name,name])) };
      if (specifier === '@expo/vector-icons') return { Ionicons: 'Icon' };
      if (specifier === '@expo/ui/community/datetime-picker') return { __esModule: true, default: 'DateTimePicker' };
      if (specifier === 'react-native-safe-area-context') return { SafeAreaView: 'SafeAreaView', SafeAreaProvider: 'SafeAreaProvider' };
      if (specifier.startsWith('.')) {
        const path = resolve(dirname(filename), specifier);
        return load(/\.(json|png)$/.test(path) ? path : existsSync(`${path}.ts`) ? `${path}.ts` : `${path}.tsx`);
      }
      return require(specifier);
    } });
    return exports;
  }
  function mount(Component, initial) {
    const state = []; let props = initial;
    const render = () => { storage = state; cursor = 0; effects = []; const result = Component(props); effects.forEach(effect => effect()); return result; };
    const nodes = () => tree(render());
    return {
      render, nodes, load,
      updateProps(next) { props = { ...props, ...next }; return render(); },
      mount: element => mount(element.type, element.props),
      find: (type, predicate = () => true) => nodes().find(node => node.type === type && predicate(node.props)),
      unmount() { state.filter(value => value?.effect).forEach(value => value.cleanup?.()); },
    };
  }
  return mount(load(resolve(root, file))[name], initialProps);
}
