import React, { useRef, useState } from 'react';
import { Alert, Text } from 'react-native';

import { useApp } from '../../context/AppContext';
import { brand } from '../../shared/brand/identity';
import { DetailPage } from '../../shared/ui/DetailPage';
import { PanelHeading, SettingsAction, SettingsPanel, settingsStyles } from '../../shared/ui/SettingsPrimitives';

export function AccountScreen({ onClose }: { onClose: () => void }) {
  const { logout, deleteAccount } = useApp();
  const locked = useRef(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  async function run(action: () => Promise<void>) {
    if (locked.current) return;
    locked.current = true; setBusy(true); setError(null);
    try { await action(); } catch (caught) { setError(caught instanceof Error ? caught.message : '操作未完成，请重试'); }
    finally { locked.current = false; setBusy(false); }
  }
  function confirmLogout() {
    Alert.alert('退出登录？', '离线待发送的消息会保留在这台设备上。', [
      { text: '取消', style: 'cancel' }, { text: '退出', style: 'destructive', onPress: () => void run(logout) },
    ]);
  }
  function confirmDelete() {
    Alert.alert('永久删除账号？', '你的对话、身体记录、目标、记忆和登录方式都会被删除，无法恢复。', [
      { text: '取消', style: 'cancel' }, { text: '我知道，继续', style: 'destructive', onPress: () => Alert.alert('最后确认', `删除后会立即退出${brand.name}。`, [
        { text: '不删除', style: 'cancel' }, { text: '永久删除', style: 'destructive', onPress: () => void run(deleteAccount) },
      ]) },
    ]);
  }
  return <DetailPage title="账号与说明" onClose={onClose} busy={busy}>
    <SettingsPanel><PanelHeading title="资料安全" icon="shield-checkmark-outline" tone="peach" /><Text style={settingsStyles.copy}>登录凭证保存在系统安全区，健康资料以服务端记录为准。</Text></SettingsPanel>
    <SettingsPanel><PanelHeading title="健康边界" icon="medkit-outline" /><Text style={settingsStyles.copy}>{brand.name}用于日常减重管理，不替代医生、营养师的诊断或治疗。</Text></SettingsPanel>
    <SettingsAction title="退出登录" variant="secondary" busy={busy} onPress={confirmLogout} />
    <SettingsAction title="永久删除账号" variant="danger" disabled={busy} onPress={confirmDelete} />
    {error && <Text accessibilityRole="alert" style={settingsStyles.error}>{error}</Text>}
  </DetailPage>;
}
