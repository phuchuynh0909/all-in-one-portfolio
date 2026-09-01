import { API_BASE_URL } from '../api';
import { UNAUTHORIZED_EVENT, clearToken, getToken } from './token';

/**
 * Wraps `window.fetch` so API requests carry the bearer token.
 *
 * Why patch the global instead of changing `apiGet`/`apiPost`: those two see
 * only a fraction of API traffic. Roughly 76 raw `fetch()` calls across 18
 * files talk to the API directly — all of `lib/services/timeseries.ts`,
 * `quote.ts`, `chat.ts`, `tradingAgents.ts`, `mvf.ts`, the portfolio CRUD
 * components, `pages/Home.tsx`. Threading a helper through every one of them is
 * a wide diff that can silently miss a call site, including any added later.
 *
 * The origin check is the load-bearing part. It must match our API and nothing
 * else: attaching the token to the TradingView CDN or any other third party
 * would hand it out. It is deliberately narrow — an absolute URL under
 * API_BASE_URL, or a same-origin `/api/...` path.
 */
function apiPrefix(): string {
  return new URL(API_BASE_URL, window.location.origin).toString();
}

export function isApiUrl(rawUrl: string): boolean {
  let resolved: URL;
  try {
    resolved = new URL(rawUrl, window.location.origin);
  } catch {
    return false;
  }

  if (resolved.toString().startsWith(apiPrefix())) return true;

  // `SectorPerformanceChart.tsx` fetches a bare `/api/v1/...` path, which only
  // resolves behind nginx rather than through API_BASE_URL.
  return (
    resolved.origin === window.location.origin &&
    resolved.pathname.startsWith('/api/')
  );
}

function urlOf(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input;
  if (input instanceof URL) return input.toString();
  return input.url;
}

export function installAuthFetch(): void {
  const original = window.fetch.bind(window);

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = urlOf(input);
    if (!isApiUrl(url)) return original(input, init);

    const headers = new Headers(
      init?.headers ?? (input instanceof Request ? input.headers : undefined),
    );
    const token = getToken();
    if (token && !headers.has('Authorization')) {
      headers.set('Authorization', `Bearer ${token}`);
    }

    const response = await original(input, { ...init, headers });

    // A 401 from the login endpoint is just a wrong password — the form shows
    // it. Anywhere else it means our token died, so drop it and let
    // AuthProvider redirect.
    if (response.status === 401 && !url.includes('/auth/login')) {
      clearToken();
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    }

    return response;
  };
}
