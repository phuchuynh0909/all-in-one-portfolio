import type { ISPAlert } from './services/ispAlerts';

export function mergeAlerts(existing: ISPAlert[], incoming: ISPAlert[]): ISPAlert[] {
  const seen = new Set<string>();
  return [...incoming, ...existing].filter((alert) => {
    const key = JSON.stringify([alert.symbol, alert.ts]);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 5000);
}
