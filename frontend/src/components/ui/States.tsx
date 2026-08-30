import type { ReactNode } from 'react';
import { Alert, AlertTitle, Box, Button, CircularProgress, Skeleton, Typography } from '@mui/material';
import InboxOutlinedIcon from '@mui/icons-material/InboxOutlined';
import RefreshIcon from '@mui/icons-material/Refresh';

/** Nothing to show — not an error. Always says what to do next. */
export function EmptyState({
  title = 'No data',
  description,
  icon,
  action,
  compact = false,
}: {
  title?: string;
  description?: ReactNode;
  icon?: ReactNode;
  action?: ReactNode;
  compact?: boolean;
}) {
  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        textAlign: 'center',
        gap: 1,
        py: compact ? 3 : 6,
        px: 2,
        color: 'text.tertiary',
      }}
    >
      <Box sx={{ color: 'text.disabled', display: 'flex' }}>
        {icon ?? <InboxOutlinedIcon sx={{ fontSize: compact ? 24 : 32 }} />}
      </Box>
      <Typography variant="subtitle2" sx={{ color: 'text.secondary' }}>
        {title}
      </Typography>
      {description && (
        <Typography variant="body2" sx={{ color: 'text.tertiary', maxWidth: '46ch' }}>
          {description}
        </Typography>
      )}
      {action && <Box sx={{ mt: 1 }}>{action}</Box>}
    </Box>
  );
}

/** Inline spinner for a panel body. */
export function LoadingState({ label = 'Loading', compact = false }: { label?: string; compact?: boolean }) {
  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 1.5,
        py: compact ? 3 : 6,
      }}
    >
      <CircularProgress size={compact ? 18 : 24} thickness={4} />
      <Typography variant="caption" sx={{ color: 'text.tertiary' }}>
        {label}
      </Typography>
    </Box>
  );
}

/** Skeleton placeholder shaped like a table, for first loads. */
export function TableSkeleton({ rows = 6, columns = 5 }: { rows?: number; columns?: number }) {
  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75, p: 1 }}>
      {Array.from({ length: rows }).map((_, r) => (
        <Box key={r} sx={{ display: 'flex', gap: 1 }}>
          {Array.from({ length: columns }).map((__, c) => (
            <Skeleton key={c} variant="rounded" height={20} sx={{ flex: c === 0 ? 1.6 : 1 }} />
          ))}
        </Box>
      ))}
    </Box>
  );
}

/** Something failed. Shows the real message and offers a retry. */
export function ErrorState({
  error,
  title = 'Something went wrong',
  onRetry,
}: {
  error?: unknown;
  title?: string;
  onRetry?: () => void;
}) {
  const message =
    error instanceof Error
      ? error.message
      : typeof error === 'string'
        ? error
        : error != null
          ? String(error)
          : undefined;

  return (
    <Alert
      severity="error"
      sx={{ m: 1 }}
      action={
        onRetry && (
          <Button color="inherit" size="small" startIcon={<RefreshIcon fontSize="small" />} onClick={onRetry}>
            Retry
          </Button>
        )
      }
    >
      <AlertTitle sx={{ fontSize: '0.8125rem', fontWeight: 600, mb: 0.25 }}>{title}</AlertTitle>
      {message && (
        <Typography variant="mono" sx={{ fontSize: '0.75rem', wordBreak: 'break-word' }}>
          {message}
        </Typography>
      )}
    </Alert>
  );
}

/**
 * Renders the right state for a react-query-shaped result.
 * Collapses the loading / error / empty / data branch that every panel repeats.
 */
export function QueryState({
  isLoading,
  error,
  isEmpty,
  onRetry,
  loadingLabel,
  emptyTitle,
  emptyDescription,
  children,
}: {
  isLoading?: boolean;
  error?: unknown;
  isEmpty?: boolean;
  onRetry?: () => void;
  loadingLabel?: string;
  emptyTitle?: string;
  emptyDescription?: ReactNode;
  children: ReactNode;
}) {
  if (isLoading) return <LoadingState label={loadingLabel} />;
  if (error) return <ErrorState error={error} onRetry={onRetry} />;
  if (isEmpty) return <EmptyState title={emptyTitle} description={emptyDescription} />;
  return <>{children}</>;
}
