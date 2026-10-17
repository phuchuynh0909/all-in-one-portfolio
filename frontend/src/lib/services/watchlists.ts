import { API_BASE_URL } from '../api';
import { getToken } from '../auth/token';
import type { WatchListSymbolList } from '../tv';

export interface WatchlistLibrary {
  lists: WatchListSymbolList[];
  active_list_id: string | null;
  imported_ids: string[];
}

export type WatchlistWrite = Pick<WatchlistLibrary, 'lists' | 'active_list_id'>;

export interface WatchlistTransport {
  load(): Promise<WatchlistLibrary>;
  initialize(state: WatchlistWrite): Promise<WatchlistLibrary>;
  save(state: WatchlistWrite): Promise<WatchlistLibrary>;
  import(state: WatchlistWrite): Promise<WatchlistLibrary>;
  dispose(): void;
}

/** Pin requests to this login and abort them when its watchlist is disposed. */
export function createWatchlistTransport(): WatchlistTransport {
  const token = getToken();
  const controller = new AbortController();
  const request = async (path = '', method = 'GET', body?: WatchlistWrite): Promise<WatchlistLibrary> => {
    const response = await fetch(`${API_BASE_URL}/watchlists${path}`, {
      method, signal: controller.signal,
      headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(body ? { 'Content-Type': 'application/json' } : {}) },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    if (!response.ok) {
      let message = `Watchlist ${method === 'GET' ? 'load' : 'save'} failed (${response.status}).`;
      try {
        const error = await response.json();
        if (typeof error.detail === 'string') message = error.detail;
        else if (Array.isArray(error.detail)) message = error.detail.map((item: { msg: string }) => item.msg).join('; ');
      } catch { /* Retain the HTTP error for non-JSON responses. */ }
      throw new Error(message);
    }
    return await response.json() as WatchlistLibrary;
  };
  return {
    load: () => request(),
    initialize: (state) => request('/initialize', 'POST', state),
    save: (state) => request('', 'PUT', state),
    import: (state) => request('/import', 'POST', state),
    dispose: () => controller.abort(),
  };
}
