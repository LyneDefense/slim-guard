import React from 'react';
import { StyleSheet, Text } from 'react-native';

import { useApp } from '../../context/AppContext';
import { DetailPage } from '../../shared/ui/DetailPage';
import { appearance as palette } from '../../shared/ui/appearance';
import { PanelHeading, SettingsAction, SettingsPanel, settingsStyles } from '../../shared/ui/SettingsPrimitives';
import { useWeComBinding } from './useWeComBinding';

export function WeComScreen({ onClose }: { onClose: () => void }) {
  const { getWeComBinding, createWeComBinding } = useApp();
  const state = useWeComBinding(getWeComBinding, createWeComBinding);
  const binding = state.binding;
  const pending = binding?.status === 'pending' && !!binding.code && Date.parse(binding.expires_at) > Date.now();
  return <DetailPage title="连接微信" onClose={onClose}>
    <SettingsPanel>
      <PanelHeading title={binding?.status === 'claimed' ? '已经连接' : '连接原来的微信记录'} icon="chatbubbles-outline" tone="blue" />
      <Text style={settingsStyles.copy}>{binding?.status === 'claimed' ? 'App 和微信客服使用同一份记录与记忆。' : binding?.status === 'conflict'
        ? '两边都有历史，系统没有擅自覆盖。请联系管理员安全合并。' : '用一次性绑定码证明微信身份，不需要提供微信密码。'}</Text>
      {pending && <>
        <Text style={settingsStyles.copy}>请在有效期内，把下面整行文字发送到已接入的微信客服：</Text>
        <Text selectable style={styles.code}>SG-{binding.code}</Text>
        <Text style={settingsStyles.copy}>有效期至 {new Date(binding.expires_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}。绑定码一次有效。</Text>
      </>}
      {binding?.status !== 'claimed' && <SettingsAction title={pending ? '我已发送，检查状态' : binding?.status === 'conflict' || state.error ? '重新检查' : '生成绑定码'}
        busy={state.busy} onPress={() => void state.request(pending || state.error || binding?.status === 'conflict' ? 'check' : 'create')} />}
      {binding?.status === 'claimed' && <SettingsAction title="检查连接状态" variant="secondary" busy={state.busy} onPress={() => void state.request()} />}
      {state.error && <Text accessibilityRole="alert" style={settingsStyles.error}>{state.error}</Text>}
    </SettingsPanel>
  </DetailPage>;
}

const styles = StyleSheet.create({ code: { color: palette.ink, fontSize: 26, fontWeight: '700', letterSpacing: 2, textAlign: 'center', paddingVertical: 20, backgroundColor: palette.lavender, borderRadius: 14 } });
