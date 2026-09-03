import { Ionicons } from '@expo/vector-icons';
import React, { useEffect, useState } from 'react';
import {
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
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
  const {
    authOptions,
    requestOtp,
    verifyOtp,
    loginWithPassword,
    loading,
    error,
    clearError,
  } = useApp();
  const [loginMode, setLoginMode] = useState<'test' | 'phone'>('phone');
  const [username, setUsername] = useState('test1');
  const [password, setPassword] = useState('123456');
  const [showPassword, setShowPassword] = useState(false);
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

  useEffect(() => {
    if (!authOptions.test_account_login_enabled) return;
    setLoginMode('test');
    setUsername((current) => (
      authOptions.test_accounts.some((account) => account.username === current)
        ? current
        : authOptions.test_accounts[0]?.username || 'test1'
    ));
  }, [authOptions]);

  function changeMode(mode: 'test' | 'phone') {
    setLoginMode(mode);
    setLocalError(null);
    clearError();
  }

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

  async function signInWithPassword() {
    if (!username.trim() || !password) {
      setLocalError('请输入测试账号和密码');
      return;
    }
    setLocalError(null);
    clearError();
    try {
      await loginWithPassword(username, password);
    } catch {
      // The shared context renders the server error below.
    }
  }

  return (
    <SafeAreaView style={styles.safe}>
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.flex}>
        <ScrollView
          contentContainerStyle={styles.page}
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}
        >
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
            {authOptions.test_account_login_enabled ? (
              <View style={styles.modeSwitch}>
                <Pressable
                  accessibilityRole="tab"
                  accessibilityState={{ selected: loginMode === 'test' }}
                  onPress={() => changeMode('test')}
                  style={[styles.modeButton, loginMode === 'test' && styles.modeButtonActive]}
                >
                  <Text style={[styles.modeText, loginMode === 'test' && styles.modeTextActive]}>
                    测试账号
                  </Text>
                </Pressable>
                <Pressable
                  accessibilityRole="tab"
                  accessibilityState={{ selected: loginMode === 'phone' }}
                  onPress={() => changeMode('phone')}
                  style={[styles.modeButton, loginMode === 'phone' && styles.modeButtonActive]}
                >
                  <Text style={[styles.modeText, loginMode === 'phone' && styles.modeTextActive]}>
                    手机号
                  </Text>
                </Pressable>
              </View>
            ) : null}

            {loginMode === 'test' && authOptions.test_account_login_enabled ? (
              <>
                <Text style={styles.accountHelp}>5 个账号的数据彼此独立，登录后可在“我的”里改名字。</Text>
                <View style={styles.accountChoices}>
                  {authOptions.test_accounts.map((account) => {
                    const selected = account.username === username.trim().toLowerCase();
                    return (
                      <Pressable
                        key={account.username}
                        onPress={() => setUsername(account.username)}
                        style={[styles.accountChoice, selected && styles.accountChoiceActive]}
                      >
                        <Text style={[styles.accountChoiceText, selected && styles.accountChoiceTextActive]}>
                          {account.username}
                        </Text>
                      </Pressable>
                    );
                  })}
                </View>
                <Text style={styles.label}>账号</Text>
                <View style={styles.inputRow}>
                  <TextInput
                    accessibilityLabel="测试账号"
                    autoCapitalize="none"
                    autoComplete="username"
                    placeholder="例如 test1"
                    placeholderTextColor="#9A9F9B"
                    value={username}
                    onChangeText={setUsername}
                    style={styles.input}
                  />
                </View>
                <Text style={[styles.label, styles.fieldGap]}>密码</Text>
                <View style={styles.inputRow}>
                  <TextInput
                    accessibilityLabel="测试账号密码"
                    autoCapitalize="none"
                    autoComplete="current-password"
                    placeholder="请输入密码"
                    placeholderTextColor="#9A9F9B"
                    secureTextEntry={!showPassword}
                    value={password}
                    onChangeText={setPassword}
                    style={styles.input}
                  />
                  <Pressable
                    accessibilityLabel={showPassword ? '隐藏密码' : '显示密码'}
                    onPress={() => setShowPassword((current) => !current)}
                    style={styles.eyeButton}
                  >
                    <Ionicons
                      name={showPassword ? 'eye-off-outline' : 'eye-outline'}
                      size={20}
                      color={colors.inkMuted}
                    />
                  </Pressable>
                </View>
                <Text style={styles.testPasswordHint}>测试密码已预填为 123456</Text>
              </>
            ) : (
              <>
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
                    <Text style={[styles.label, styles.fieldGap]}>验证码</Text>
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
                        <Text style={[styles.change, seconds > 0 && styles.disabled]}>
                          {seconds > 0 ? `${seconds}s` : '重发'}
                        </Text>
                      </Pressable>
                    </View>
                    {challenge.debug_code ? <Text style={styles.debug}>本地测试验证码已自动填入</Text> : null}
                  </>
                ) : null}
              </>
            )}
            {(localError || error) ? <Text style={styles.error}>{localError || error}</Text> : null}
            <Button
              title={loginMode === 'test' ? '登录测试账号' : challenge ? '进入 SlimGuard' : '获取验证码'}
              icon={loginMode === 'test' || challenge ? 'arrow-forward' : 'chatbubble-ellipses-outline'}
              loading={loading}
              onPress={loginMode === 'test' ? signInWithPassword : challenge ? signIn : sendCode}
              style={{ marginTop: spacing.lg }}
            />
          </View>
          <Text style={styles.terms}>继续即代表你同意服务与隐私规则。健康建议仅用于日常管理，不能替代医生诊断。</Text>
          {__DEV__ ? <Text style={styles.dev}>API: {API_BASE_URL}</Text> : null}
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  safe: { flex: 1, backgroundColor: colors.background },
  page: { flexGrow: 1, paddingHorizontal: spacing.xl, paddingTop: spacing.lg, paddingBottom: spacing.lg },
  hero: { flex: 1, justifyContent: 'center', minHeight: 280 },
  heroArt: { width: 92, height: 82, marginBottom: spacing.lg },
  heroOrb: { width: 72, height: 72, borderRadius: 28, backgroundColor: colors.primarySoft, alignItems: 'center', justifyContent: 'center' },
  heroLeaf: { position: 'absolute', right: 0, bottom: 0, width: 42, height: 42, borderRadius: 16, backgroundColor: colors.accent, alignItems: 'center', justifyContent: 'center', transform: [{ rotate: '8deg' }] },
  title: { color: colors.ink, fontSize: 34, lineHeight: 43, letterSpacing: -1.1, fontWeight: '800' },
  subtitle: { color: colors.inkMuted, fontSize: 16, lineHeight: 25, marginTop: spacing.md, maxWidth: 360 },
  form: { backgroundColor: colors.surface, borderRadius: radius.lg, padding: spacing.lg, borderWidth: StyleSheet.hairlineWidth, borderColor: colors.line },
  modeSwitch: { flexDirection: 'row', padding: 4, borderRadius: radius.md, backgroundColor: colors.surfaceMuted, marginBottom: spacing.lg },
  modeButton: { flex: 1, minHeight: 38, borderRadius: radius.sm, alignItems: 'center', justifyContent: 'center' },
  modeButtonActive: { backgroundColor: colors.surface },
  modeText: { color: colors.inkMuted, fontSize: 13, fontWeight: '700' },
  modeTextActive: { color: colors.primaryDark },
  accountHelp: { color: colors.inkMuted, fontSize: 12, lineHeight: 18, marginBottom: spacing.md },
  accountChoices: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, marginBottom: spacing.lg },
  accountChoice: { minWidth: 60, minHeight: 34, paddingHorizontal: spacing.md, borderRadius: radius.sm, borderWidth: 1, borderColor: colors.line, alignItems: 'center', justifyContent: 'center' },
  accountChoiceActive: { borderColor: colors.primary, backgroundColor: colors.primarySoft },
  accountChoiceText: { color: colors.inkMuted, fontSize: 12, fontWeight: '700' },
  accountChoiceTextActive: { color: colors.primaryDark },
  label: { color: colors.ink, fontSize: 13, fontWeight: '700', marginBottom: 7 },
  fieldGap: { marginTop: spacing.lg },
  inputRow: { minHeight: 52, backgroundColor: colors.surfaceMuted, borderRadius: radius.md, flexDirection: 'row', alignItems: 'center', paddingHorizontal: spacing.lg },
  input: { flex: 1, color: colors.ink, fontSize: 17, paddingVertical: 12 },
  codeInput: { fontSize: 22, letterSpacing: 5, fontWeight: '700' },
  eyeButton: { padding: spacing.sm },
  change: { color: colors.primary, fontWeight: '700', padding: 8 },
  disabled: { color: colors.inkMuted },
  debug: { color: colors.primary, fontSize: 12, marginTop: spacing.sm },
  testPasswordHint: { color: colors.primary, fontSize: 12, marginTop: spacing.sm },
  error: { color: colors.danger, fontSize: 13, marginTop: spacing.md },
  terms: { color: colors.inkMuted, textAlign: 'center', fontSize: 11, lineHeight: 17, marginTop: spacing.md, paddingHorizontal: spacing.md },
  dev: { color: colors.inkMuted, textAlign: 'center', fontSize: 9, marginTop: spacing.xs },
});
