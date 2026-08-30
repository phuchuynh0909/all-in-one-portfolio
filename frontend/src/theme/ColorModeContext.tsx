import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { ThemeProvider } from '@mui/material/styles';
import CssBaseline from '@mui/material/CssBaseline';
import { createAppTheme } from './createAppTheme';
import { applyCssVars, type ColorMode } from './tokens';

const STORAGE_KEY = 'aiop.color-mode';

/** Dark-first: we only fall back to the system preference if it says light. */
export function resolveInitialMode(): ColorMode {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'dark' || stored === 'light') return stored;
  } catch {
    // Private mode / blocked storage — fall through to the default.
  }
  return 'dark';
}

interface ColorModeContextValue {
  mode: ColorMode;
  toggleMode: () => void;
  setMode: (mode: ColorMode) => void;
}

const ColorModeContext = createContext<ColorModeContextValue>({
  mode: 'dark',
  toggleMode: () => {},
  setMode: () => {},
});

export function useColorMode(): ColorModeContextValue {
  return useContext(ColorModeContext);
}

export function ColorModeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ColorMode>(resolveInitialMode);

  // Push the token set onto :root so non-MUI consumers (Bokeh, TradingView,
  // lightweight-charts, plain CSS) stay in sync with the MUI palette.
  useEffect(() => {
    applyCssVars(mode);
    try {
      localStorage.setItem(STORAGE_KEY, mode);
    } catch {
      // Persisting is best-effort.
    }
  }, [mode]);

  const setMode = useCallback((next: ColorMode) => setModeState(next), []);
  const toggleMode = useCallback(
    () => setModeState((m) => (m === 'dark' ? 'light' : 'dark')),
    [],
  );

  const theme = useMemo(() => createAppTheme(mode), [mode]);
  const value = useMemo(() => ({ mode, toggleMode, setMode }), [mode, toggleMode, setMode]);

  return (
    <ColorModeContext.Provider value={value}>
      <ThemeProvider theme={theme}>
        <CssBaseline />
        {children}
      </ThemeProvider>
    </ColorModeContext.Provider>
  );
}
