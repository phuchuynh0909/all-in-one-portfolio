const TOKEN_KEY = 'auth_token';

/** Dispatched on `window` when the API rejects our token. */
export const UNAUTHORIZED_EVENT = 'auth:unauthorized';

/**
 * Every accessor is wrapped: localStorage throws outright in a Safari private
 * window and wherever site data is blocked, and a storage failure must not take
 * the whole app down with it.
 */
export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // Session lasts only as long as the tab. Better than refusing to log in.
  }
}

export function clearToken(): void {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    // Nothing to do; the token was never persisted.
  }
}
