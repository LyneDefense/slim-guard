export type ConnectionState = {
  device: 'unknown' | 'connected' | 'disconnected';
  backend: 'unknown' | 'reachable' | 'unavailable' | 'degraded';
};

// Transport observations, not chat-processing state. An older probe may never
// overrule a newer API response or a physical network transition.
export class ConnectionMonitor {
  private state: ConnectionState = {
    device: 'unknown',
    backend: 'unknown'
  };
  private revision = 0;
  private listeners = new Set<(state: ConnectionState) => void>();
  snapshot = () => this.state;
  subscribe = (listener: (state: ConnectionState) => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  private update(patch: Partial<ConnectionState>) {
    this.revision++;
    const next = {
      ...this.state,
      ...patch
    };
    if (next.device === this.state.device && next.backend === this.state.backend) return;
    this.state = next;
    this.listeners.forEach(listener => listener(next));
  }
  device(connected: boolean | null) {
    const device = connected === null ? 'unknown' : connected ? 'connected' : 'disconnected';
    if (device !== this.state.device) this.update({
      device,
      backend: 'unknown'
    });
  }
  response(status: number) {
    this.update({
      backend: status >= 500 ? 'degraded' : 'reachable'
    });
  }
  failure(code: string) {
    // A timed-out POST may still be processing. It is not evidence of an offline device.
    if (code === 'network_unavailable') this.update({
      backend: 'unavailable'
    });
  }
  async probe(check: () => Promise<boolean>) {
    if (this.state.device === 'disconnected') return;
    const revision = this.revision;
    let reachable = false;
    try {
      reachable = await check();
    } catch {/* Probe failure is distinct from physical disconnection. */}
    if (revision === this.revision) this.update({
      backend: reachable ? 'reachable' : 'unavailable'
    });
  }
}
export const connectionMonitor = new ConnectionMonitor();
export function connectionLabel(state: ConnectionState): string {
  if (state.device === 'disconnected') return '离线模式';
  if (state.backend === 'unavailable') return '暂时连不上服务';
  if (state.backend === 'degraded') return '服务暂不可用';
  return state.backend === 'reachable' ? '教练在线' : '连接中';
}
