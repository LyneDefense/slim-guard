import { Ionicons } from '@expo/vector-icons';
import React from 'react';
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  View,
  type PressableProps,
  type ViewStyle,
} from 'react-native';

import { colors, radius, spacing } from '../theme';

export function BrandMark({ compact = false }: { compact?: boolean }) {
  return (
    <View style={styles.brandRow}>
      <View style={[styles.brandIcon, compact && styles.brandIconCompact]}>
        <Ionicons name="leaf" size={compact ? 17 : 23} color={colors.white} />
      </View>
      <Text style={[styles.brandText, compact && styles.brandTextCompact]}>SlimGuard</Text>
    </View>
  );
}

export function Card({ children, style }: { children: React.ReactNode; style?: ViewStyle | ViewStyle[] }) {
  return <View style={[styles.card, style]}>{children}</View>;
}

type ButtonProps = PressableProps & {
  title: string;
  icon?: keyof typeof Ionicons.glyphMap;
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  loading?: boolean;
};

export function Button({ title, icon, variant = 'primary', loading, disabled, style, ...props }: ButtonProps) {
  const textColor = variant === 'primary' ? colors.white : variant === 'danger' ? colors.danger : colors.primaryDark;
  return (
    <Pressable
      accessibilityRole="button"
      disabled={disabled || loading}
      style={({ pressed }) => [
        styles.button,
        styles[`button_${variant}`],
        (disabled || loading) && styles.buttonDisabled,
        pressed && styles.buttonPressed,
        typeof style === 'function' ? style({ pressed }) : style,
      ]}
      {...props}
    >
      {loading ? <ActivityIndicator color={textColor} /> : (
        <>
          {icon ? <Ionicons name={icon} size={18} color={textColor} /> : null}
          <Text style={[styles.buttonText, { color: textColor }]}>{title}</Text>
        </>
      )}
    </Pressable>
  );
}

export function SectionTitle({ title, action }: { title: string; action?: React.ReactNode }) {
  return (
    <View style={styles.sectionTitleRow}>
      <Text style={styles.sectionTitle}>{title}</Text>
      {action}
    </View>
  );
}

export function EmptyState({ icon, title, body }: { icon: keyof typeof Ionicons.glyphMap; title: string; body: string }) {
  return (
    <View style={styles.empty}>
      <View style={styles.emptyIcon}><Ionicons name={icon} size={24} color={colors.primary} /></View>
      <Text style={styles.emptyTitle}>{title}</Text>
      <Text style={styles.emptyBody}>{body}</Text>
    </View>
  );
}

export function LoadingScreen() {
  return (
    <View style={styles.loadingScreen}>
      <BrandMark />
      <ActivityIndicator size="large" color={colors.primary} style={{ marginTop: spacing.xl }} />
      <Text style={styles.loadingText}>正在整理你的减脂进度…</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  brandRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  brandIcon: { width: 46, height: 46, borderRadius: 16, backgroundColor: colors.primary, alignItems: 'center', justifyContent: 'center' },
  brandIconCompact: { width: 34, height: 34, borderRadius: 12 },
  brandText: { color: colors.ink, fontSize: 27, fontWeight: '800', letterSpacing: -0.8 },
  brandTextCompact: { fontSize: 21 },
  card: {
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.line,
    padding: spacing.lg,
    shadowColor: colors.shadow,
    shadowOffset: { width: 0, height: 5 },
    shadowOpacity: 0.05,
    shadowRadius: 14,
    elevation: 2,
  },
  button: { minHeight: 48, borderRadius: radius.md, paddingHorizontal: spacing.lg, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8 },
  button_primary: { backgroundColor: colors.primary },
  button_secondary: { backgroundColor: colors.primarySoft },
  button_ghost: { backgroundColor: 'transparent' },
  button_danger: { backgroundColor: '#F8E4E1' },
  buttonDisabled: { opacity: 0.45 },
  buttonPressed: { opacity: 0.78, transform: [{ scale: 0.99 }] },
  buttonText: { fontSize: 15, fontWeight: '700' },
  sectionTitleRow: { marginTop: spacing.xl, marginBottom: spacing.md, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  sectionTitle: { color: colors.ink, fontSize: 19, fontWeight: '800', letterSpacing: -0.3 },
  empty: { alignItems: 'center', paddingVertical: spacing.xl, paddingHorizontal: spacing.lg },
  emptyIcon: { width: 48, height: 48, borderRadius: 18, backgroundColor: colors.primarySoft, alignItems: 'center', justifyContent: 'center', marginBottom: spacing.md },
  emptyTitle: { color: colors.ink, fontSize: 17, fontWeight: '700' },
  emptyBody: { color: colors.inkMuted, fontSize: 14, lineHeight: 21, textAlign: 'center', marginTop: spacing.xs },
  loadingScreen: { flex: 1, backgroundColor: colors.background, alignItems: 'center', justifyContent: 'center' },
  loadingText: { color: colors.inkMuted, marginTop: spacing.md, fontSize: 14 },
});
