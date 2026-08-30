import type { ReactNode } from 'react';
import { Box, Paper, Typography, type SxProps, type Theme } from '@mui/material';

export interface PanelProps {
  title?: ReactNode;
  /** Small muted line under the title. */
  subtitle?: ReactNode;
  /** Rendered at the right of the header — filters, actions, legends. */
  actions?: ReactNode;
  children: ReactNode;
  /** Removes body padding — for tables and charts that bleed to the edge. */
  flush?: boolean;
  /** Tighter body padding. */
  dense?: boolean;
  /** Body fills remaining height and scrolls internally. */
  fill?: boolean;
  sx?: SxProps<Theme>;
}

/**
 * The standard surface for everything on a page. Replaces ad-hoc <Paper sx={{...}}>
 * so panel chrome (border, radius, header rhythm) is defined in exactly one place.
 */
export default function Panel({
  title,
  subtitle,
  actions,
  children,
  flush = false,
  dense = false,
  fill = false,
  sx,
}: PanelProps) {
  const hasHeader = Boolean(title || actions || subtitle);
  const pad = dense ? 1.5 : 2;

  return (
    <Paper
      sx={{
        display: 'flex',
        flexDirection: 'column',
        minWidth: 0,
        ...(fill && { height: '100%', overflow: 'hidden' }),
        ...sx,
      }}
    >
      {hasHeader && (
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 2,
            px: pad,
            py: 1.25,
            borderBottom: 1,
            borderColor: 'line.subtle',
            flexShrink: 0,
          }}
        >
          <Box sx={{ minWidth: 0 }}>
            {title && (
              <Typography variant="subtitle2" sx={{ color: 'text.primary', lineHeight: 1.3 }} noWrap>
                {title}
              </Typography>
            )}
            {subtitle && (
              <Typography variant="caption" sx={{ color: 'text.tertiary' }} noWrap component="div">
                {subtitle}
              </Typography>
            )}
          </Box>
          {actions && (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexShrink: 0 }}>{actions}</Box>
          )}
        </Box>
      )}
      <Box
        sx={{
          p: flush ? 0 : pad,
          minWidth: 0,
          ...(fill && { flex: 1, overflow: 'auto' }),
        }}
      >
        {children}
      </Box>
    </Paper>
  );
}
