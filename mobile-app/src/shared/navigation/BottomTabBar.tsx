import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { AppIcon } from '../ui/AppIcon';
import { appearance as palette } from '../ui/appearance';

export type Tab = 'today' | 'coach' | 'progress' | 'profile';
const tabs = [
  { id: 'today', title: '今天', icon: 'sun' },
  { id: 'coach', title: '教练', icon: 'chat' },
  { id: 'progress', title: '趋势', icon: 'progress' },
  { id: 'profile', title: '我的', icon: 'profile' },
] as const;

export function BottomTabBar({ selected, bottomInset, onSelect }: {
  selected: Tab; bottomInset: number; onSelect: (tab: Tab) => void;
}) {
  return <View style={[styles.bar, { paddingBottom: Math.max(bottomInset, 8) }]}>
    {tabs.map(item => {
      const active = selected === item.id;
      return <Pressable key={item.id} accessibilityRole="tab" accessibilityLabel={item.title}
        accessibilityState={{ selected: active }} onPress={() => onSelect(item.id)} style={styles.tab}>
        <View style={[styles.icon, active && styles.activeIcon]}>
          <AppIcon name={item.icon} size={26} color={active ? item.id !== 'today' ? palette.apricot : palette.surface : '#666B7A'}
            accent={active && item.id === 'today' ? palette.apricot : undefined} />
        </View>
        <Text style={[styles.label, active && styles.activeLabel]}>{item.title}</Text>
      </Pressable>;
    })}
  </View>;
}

const styles = StyleSheet.create({
  bar: { flexDirection: 'row', paddingTop: 6, backgroundColor: palette.surface, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: palette.line },
  tab: { flex: 1, alignItems: 'center', gap: 3, minHeight: 52 },
  icon: { width: 34, height: 32, borderRadius: 13, alignItems: 'center', justifyContent: 'center' },
  activeIcon: { backgroundColor: '#303E79' },
  label: { color: '#666B7A', fontSize: 12, lineHeight: 17, fontWeight: '600' },
  activeLabel: { color: palette.ink, fontWeight: '700' },
});
