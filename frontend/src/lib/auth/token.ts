const TOKEN_KEY = 'auth_token';
let memoryToken: string | null = null;
let useMemoryToken = false;

/** Dispatched on `window` when the API rejects our token. */
export const UNAUTHORIZED_EVENT = 'auth:unauthorized';

/**
 * Every accessor is wrapped: localStorage throws outright in a Safari private
 * window and wherever site data is blocked, and a storage failure must not take
 * the whole app down with it.
 */
export function getToken(): string | null {
  if (useMemoryToken) return memoryToken;
  try {
    memoryToken = localStorage.getItem(TOKEN_KEY);
    return memoryToken;
  } catch {
    return memoryToken;
  }
}

export function setToken(token: string): void {
  memoryToken = token;
  try {
    localStorage.setItem(TOKEN_KEY, token);
    useMemoryToken = false;
  } catch {
    useMemoryToken = true;
  }
}

export function clearToken(): void {
  memoryToken = null;
  try {
    localStorage.removeItem(TOKEN_KEY);
    useMemoryToken = false;
  } catch {
    useMemoryToken = true;
  }
}
