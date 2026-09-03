import { Ionicons } from '@expo/vector-icons';
import React, { useEffect, useState } from 'react';
import {
  KeyboardAvoidingView,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { BrandMark, Button } from '../components/ui';
import { useApp } from '../context/AppContext';
import { API_BASE_URL } from '../lib/api';
import { colors, radius, spacing } from '../theme';
import type { OtpChallenge } from '../types';

export function AuthScreen() {
  const { requestOtp, verifyOtp, loading, error, clearError } = useApp();
  const [phone, setPhone] = useState('');
  const [code, setCode] = useState('');
  const [challenge, setChallenge] = useState<OtpChallenge | null>(null);
  const [seconds, setSeconds] = useState(0);
  const [localError, setLocalError] = useState<string | null>(null);

  useEffect(() => {
    if (seconds <= 0) return;
    const timer = setInterval(() => setSeconds((current) => Math.max(0, current - 1)), 1000);
    return () => clearInterval(timer);
  }, [seconds]);

  async function sendCode() {
    const normalized = phone.replace(/\s/g, '');
    if (!/^\+?\d{6,20}$/.test(normalized)) {
      setLocalError('请输入可接收短信的手机号');
      return;
    }
    setLocalError(null);
    clearError();
    try {
      const next = await requestOtp(normalized);
      setChallenge(next);
      setSeconds(next.retry_after_seconds);
      if (next.debug_code) setCode(next.debug_code);
    } catch (caught) {
      setLocalError(caught instanceof Error ? caught.message : '验证码发送失败');
    }
  }

  async function signIn() {
    if (!challenge || !/^\d{6}$/.test(code)) {
      setLocalError('请输入 6 位验证码');
      return;
    }
    setLocalError(null);
    try {
      await verifyOtp(challenge.challenge_id, code);
    } catch {
      // The shared context renders the server error below.
    }
  }

  return (
    <SafeAreaView style={styles.safe}>
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.flex}>
        <View style={styles.page}>
          <BrandMark />
          <View style={styles.hero}>
            <View style={styles.heroArt}>
              <View style={styles.heroOrb}><Ionicons name="sparkles" size={34} color={colors.primaryDark} /></View>
              <View style={styles.heroLeaf}><Ionicons name="leaf" size={24} color={colors.white} /></View>
            </View>
            <Text style={styles.title}>一个真正记得你的{`\n`}减脂教练</Text>
            <Text style={styles.subtitle}>不评判，不套模板。你的记录、目标和习惯会慢慢连成一条清晰的路。</Text>
          </View>

          <View style={styles.form}>
            <Text style={styles.label}>手机号</Text>
            <View style={styles.inputRow}>
              <TextInput
                accessibilityLabel="手机号"
                autoComplete="tel"
                keyboardType="phone-pad"
                placeholder="例如 138 0000 0000"
                placeholderTextColor="#9A9F9B"
                value={phone}
                onChangeText={setPhone}
                editable={!challenge}
                style={styles.input}
              />
              {challenge ? (
                <Pressable onPress={() => { setChallenge(null); setCode(''); clearError(); }}>
                  <Text style={styles.change}>更换</Text>
                </Pressable>
              ) : null}
            </View>
            {challenge ? (
              <>
                <Text style={[styles.label, { marginTop: spacing.lg }]}>验证码</Text>
                <View style={styles.inputRow}>
                  <TextInput
                    accessibilityLabel="短信验证码"
                    autoComplete="sms-otp"
                    keyboardType="number-pad"
                    maxLength={6}
                    placeholder="6 位数字"
                    placeholderTextColor="#9A9F9B"
                    value={code}
                    onChangeText={(value) => setCode(value.replace(/\D/g, ''))}
                    style={[styles.input, styles.codeInput]}
                  />
                  <Pressable disabled={seconds > 0} onPress={sendCode}>
                    <Text style={[styles.change, seconds > 0 && styles.disabled]}>{seconds > 0 ? `${seconds}s` : '重发'}</Text>
                  </Pressable>
                </View>
                {challenge.debug_code ? <Text style={styles.debug}>本地测试验证码已自动填入</Text> : null}
              </>
            ) : null}
            {(localError || error) ? <Text style={styles.error}>{localError || error}</Text> : null}
            <Button
              title={challenge ? '进入 SlimGuard' : '获取验证码'}
              icon={challenge ? 'arrow-forward' : 'chatbubble-ellipses-outline'}
              loading={loading}
              onPress={challenge ? signIn : sendCode}
              style={{ marginTop: spacing.lg }}
            />
          </View>
          <Text style={styles.terms}>继续即代表你同意服务与隐私规则。健康建议仅用于日常管理，不能替代医生诊断。</Text>
          {__DEV__ ? <Text style={styles.dev}>API: {API_BASE_URL}</Text> : null}
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  safe: { flex: 1, backgroundColor: colors.background },
  page: { flex: 1, paddingHorizontal: spacing.xl, paddingTop: spacing.lg, paddingBottom: spacing.lg },
  hero: { flex: 1, justifyContent: 'center', minHeight: 280 },
  heroArt: { width: 92, height: 82, marginBottom: spacing.lg },
  heroOrb: { width: 72, height: 72, borderRadius: 28, backgroundColor: colors.primarySoft, alignItems: 'center', justifyContent: 'center' },
  heroLeaf: { position: 'absolute', right: 0, bottom: 0, width: 42, height: 42, borderRadius: 16, backgroundColor: colors.accent, alignItems: 'center', justifyContent: 'center', transform: [{ rotate: '8deg' }] },
  title: { color: colors.ink, fontSize: 34, lineHeight: 43, letterSpacing: -1.1, fontWeight: '800' },
  subtitle: { color: colors.inkMuted, fontSize: 16, lineHeight: 25, marginTop: spacing.md, maxWidth: 360 },
  form: { backgroundColor: colors.surface, borderRadius: radius.lg, padding: spacing.lg, borderWidth: StyleSheet.hairlineWidth, borderColor: colors.line },
  label: { color: colors.ink, fontSize: 13, fontWeight: '700', marginBottom: 7 },
  inputRow: { minHeight: 52, backgroundColor: colors.surfaceMuted, borderRadius: radius.md, flexDirection: 'row', alignItems: 'center', paddingHorizontal: spacing.lg },
  input: { flex: 1, color: colors.ink, fontSize: 17, paddingVertical: 12 },
  codeInput: { fontSize: 22, letterSpacing: 5, fontWeight: '700' },
  change: { color: colors.primary, fontWeight: '700', padding: 8 },
  disabled: { color: colors.inkMuted },
  debug: { color: colors.primary, fontSize: 12, marginTop: spacing.sm },
  error: { color: colors.danger, fontSize: 13, marginTop: spacing.md },
  terms: { color: colors.inkMuted, textAlign: 'center', fontSize: 11, lineHeight: 17, marginTop: spacing.md, paddingHorizontal: spacing.md },
  dev: { color: colors.inkMuted, textAlign: 'center', fontSize: 9, marginTop: spacing.xs },
});
