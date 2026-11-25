import { apiPost } from '../api';

export interface TriggerResponse {
  flow_run_id: string;
  started: boolean;
  detail: string;
}

export async function syncStock(symbol: string): Promise<TriggerResponse> {
  const path = `/workflows/sync-stock/${encodeURIComponent(symbol)}`;
  return await apiPost<TriggerResponse>(path, {});
}


