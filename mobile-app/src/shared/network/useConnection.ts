import NetInfo from '@react-native-community/netinfo';
import { useEffect, useState } from 'react';
import { AppState } from 'react-native';
import { API_BASE_URL } from '../../lib/api';
import { connectionMonitor } from './connection';
export async function checkBackend(): Promise<boolean> {
  const controller = new AbortController(),
    timer = setTimeout(() => controller.abort(), 5_000);
  try {
    const response = await fetch(`${API_BASE_URL}/health/live`, {
      method: 'GET',
      cache: 'no-store',
      signal: controller.signal
    });
    return response.status === 200 && (await response.json()).status === 'ok';
  } finally {
    clearTimeout(timer);
  }
}
export function useConnection() {
  const [connection, setConnection] = useState(connectionMonitor.snapshot);
  useEffect(() => {
    // NetInfo only supplies device connectivity. Our revisioned probe below
    // replaces its Google check; InternetReachable=false is never a send gate.
    NetInfo.configure({
      reachabilityShouldRun: () => false
    });
    const unsubscribe = connectionMonitor.subscribe(setConnection);
    let active = true,
      checking = false;
    const probe = async () => {
      if (!active || checking) return;
      checking = true;
      try {
        await connectionMonitor.probe(checkBackend);
      } finally {
        checking = false;
      }
    };
    const network = NetInfo.addEventListener(state => {
      const previous = connectionMonitor.snapshot().device;
      connectionMonitor.device(state.isConnected);
      if (previous !== connectionMonitor.snapshot().device) void probe();
    });
    const foreground = AppState.addEventListener('change', state => {
      if (state === 'active') {
        void NetInfo.fetch().then(state => connectionMonitor.device(state.isConnected)).catch(() => {});
        void probe();
      }
    });
    const timer = setInterval(() => {
      if (AppState.currentState !== 'background') void probe();
    }, 30_000);
    void probe();
    setConnection(connectionMonitor.snapshot());
    return () => {
      active = false;
      unsubscribe();
      network();
      foreground.remove();
      clearInterval(timer);
    };
  }, []);
  return connection;
}
