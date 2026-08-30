import { Component, type ErrorInfo, type ReactNode } from 'react';
import { Box, Button, Typography } from '@mui/material';
import ReportProblemOutlinedIcon from '@mui/icons-material/ReportProblemOutlined';

interface Props {
  children: ReactNode;
  /** Changing this resets the boundary — pass the route so navigation clears an error. */
  resetKey?: string;
}
interface State {
  error: Error | null;
}

/** Keeps one thrown page from blanking the whole terminal. */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[ErrorBoundary]', error, info.componentStack);
  }

  componentDidUpdate(prev: Props) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null });
    }
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <Box
        sx={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 1.5,
          p: 4,
          textAlign: 'center',
        }}
      >
        <ReportProblemOutlinedIcon sx={{ fontSize: 32, color: 'warning.main' }} />
        <Typography variant="h5">This panel crashed</Typography>
        <Typography variant="body2" sx={{ color: 'text.secondary', maxWidth: '60ch' }}>
          The rest of the terminal is still running. The error is below and in the console.
        </Typography>
        <Box
          component="pre"
          sx={{
            mt: 1,
            p: 1.5,
            maxWidth: '100%',
            overflow: 'auto',
            bgcolor: 'surface.inset',
            border: 1,
            borderColor: 'line.subtle',
            borderRadius: 1,
            fontSize: '0.75rem',
            color: 'error.main',
            textAlign: 'left',
          }}
        >
          {error.message}
        </Box>
        <Button variant="outlined" size="small" onClick={() => this.setState({ error: null })} sx={{ mt: 1 }}>
          Try again
        </Button>
      </Box>
    );
  }
}
