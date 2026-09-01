import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { useQueryClient } from '@tanstack/react-query';

import { UNAUTHORIZED_EVENT, clearToken, getToken, setToken } from '../../lib/auth/token';
import { fetchMe, login, type AuthUser } from '../../lib/services/auth';

export type AuthStatus = 'loading' | 'authed' | 'anon';

type AuthContextValue = {
  user: AuthUser | null;
  status: AuthStatus;
  signIn: (username: string, password: string) => Promise<void>;
  signOut: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth must be used inside <AuthProvider>');
  return value;
}

export default function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [status, setStatus] = useState<AuthStatus>(getToken() ? 'loading' : 'anon');

  const signOut = useCallback(() => {
    clearToken();
    setUser(null);
    setStatus('anon');
    // Otherwise the next person to log in sees the previous user's cached
    // portfolio flash on screen before the refetch lands.
    queryClient.clear();
  }, [queryClient]);

  // A stored token proves nothing: it may be expired, or its user deactivated.
  // /auth/me is the cheapest way to ask the server.
  useEffect(() => {
    if (!getToken()) return;
    let cancelled = false;
    fetchMe()
      .then((me) => {
        if (cancelled) return;
        setUser(me);
        setStatus('authed');
      })
      .catch(() => {
        if (cancelled) return;
        clearToken();
        setUser(null);
        setStatus('anon');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // The interceptor fires this when any API call comes back 401.
  useEffect(() => {
    const onUnauthorized = () => signOut();
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, [signOut]);

  const signIn = useCallback(async (username: string, password: string) => {
    const { access_token } = await login(username, password);
    setToken(access_token);
    const me = await fetchMe();
    setUser(me);
    setStatus('authed');
  }, []);

  const value = useMemo(
    () => ({ user, status, signIn, signOut }),
    [user, status, signIn, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
