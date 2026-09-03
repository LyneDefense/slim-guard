import * as Notifications from 'expo-notifications';
import Constants from 'expo-constants';
import * as Crypto from 'expo-crypto';
import * as Device from 'expo-device';
import { Platform } from 'react-native';

import type { Routine } from '../types';
import { mobileApi } from './api';
import { readInstallationId, saveInstallationId } from './session';

const ids = {
  weight: 'slimguard-routine-weight',
  meal: 'slimguard-routine-meal',
  daily: 'slimguard-routine-review',
};

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldPlaySound: false,
    shouldSetBadge: false,
    shouldShowBanner: true,
    shouldShowList: true,
  }),
});

export async function syncRoutineNotifications(routine: Routine): Promise<boolean> {
  await Promise.all(Object.values(ids).map((id) => Notifications.cancelScheduledNotificationAsync(id)));
  const reminders = [
    { id: ids.weight, time: routine.weight_reminder_time, title: '量一下体重吧', body: '一分钟记下来，趋势比单次数字更重要。' },
    { id: ids.meal, time: routine.meal_reminder_time, title: '今天吃得怎么样？', body: '拍张照或说一句，SlimGuard 帮你记。' },
    { id: ids.daily, time: routine.daily_review_time, title: '今天辛苦了', body: '花半分钟复盘一下，不求完美，只看下一步。' },
  ].filter((item) => item.time !== null);

  if (reminders.length === 0) return true;
  if (Platform.OS === 'android') {
    await Notifications.setNotificationChannelAsync('routines', {
      name: '日常提醒',
      importance: Notifications.AndroidImportance.DEFAULT,
    });
  }
  const current = await Notifications.getPermissionsAsync();
  const permission = current.status === 'granted' ? current : await Notifications.requestPermissionsAsync();
  if (permission.status !== 'granted') return false;

  for (const reminder of reminders) {
    const [hour, minute] = reminder.time!.split(':').map(Number);
    await Notifications.scheduleNotificationAsync({
      identifier: reminder.id,
      content: { title: reminder.title, body: reminder.body, data: { screen: 'today' } },
      trigger: {
        type: Notifications.SchedulableTriggerInputTypes.DAILY,
        hour,
        minute,
        channelId: Platform.OS === 'android' ? 'routines' : undefined,
      },
    });
  }
  await registerRemotePush().catch(() => undefined);
  return true;
}

async function registerRemotePush(): Promise<void> {
  if (!Device.isDevice || Platform.OS === 'web') return;
  const projectId = Constants.expoConfig?.extra?.eas?.projectId ?? Constants.easConfig?.projectId;
  if (!projectId) return;
  const pushToken = (await Notifications.getExpoPushTokenAsync({ projectId })).data;
  let installationId = await readInstallationId();
  if (!installationId) {
    installationId = Crypto.randomUUID();
    await saveInstallationId(installationId);
  }
  await mobileApi.registerDevice({
    installation_id: installationId,
    platform: Platform.OS as 'ios' | 'android',
    push_provider: 'expo',
    push_token: pushToken,
    app_version: Constants.expoConfig?.version || null,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai',
    locale: Intl.DateTimeFormat().resolvedOptions().locale || null,
  });
}
