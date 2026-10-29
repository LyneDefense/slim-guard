import { useEffect, useRef, useState } from 'react';

import type { WeComBinding } from '../../types';

export function useWeComBinding(getBinding: () => Promise<WeComBinding | null>, createBinding: () => Promise<WeComBinding>) {
  const [binding, setBinding] = useState<WeComBinding | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(false), locked = useRef(false), generation = useRef(0);
  async function request(action: 'check' | 'create' = 'check') {
    if (locked.current) return;
    const ticket = ++generation.current;
    locked.current = true; setBusy(true); setError(null);
    try {
      const result = await (action === 'create' ? createBinding() : getBinding());
      if (mounted.current && ticket === generation.current) setBinding(result);
    } catch (caught) {
      if (mounted.current && ticket === generation.current) setError(caught instanceof Error ? caught.message : '暂时无法读取微信连接状态');
    } finally {
      if (ticket === generation.current) { locked.current = false; if (mounted.current) setBusy(false); }
    }
  }
  useEffect(() => {
    mounted.current = true; locked.current = false; void request();
    return () => { mounted.current = false; generation.current++; };
  }, [getBinding, createBinding]);
  return { binding, busy, error, request };
}

export function bindingLabel(binding: WeComBinding | null, busy: boolean, error: string | null): string {
  if (busy) return '读取中…';
  if (error) return '读取失败';
  if (binding?.status === 'claimed') return '已连接';
  if (binding?.status === 'conflict') return '需要处理';
  if (binding?.status === 'pending' && Date.parse(binding.expires_at) > Date.now()) return '待连接';
  return '未连接';
}
