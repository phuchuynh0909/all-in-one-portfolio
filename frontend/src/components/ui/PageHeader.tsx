import type { ReactNode } from 'react';
import { Box, Typography } from '@mui/material';

export interface PageHeaderProps {
  title: ReactNode;
  description?: ReactNode;
  /** Buttons, selectors, refresh controls. */
  actions?: ReactNode;
  /** Tabs or filter bar rendered flush under the header. */
  below?: ReactNode;
}

export default function PageHeader({ title, description, actions, below }: PageHeaderProps) {
  return (
    <Box sx={{ mb: 2.5 }}>
      <Box
        sx={{
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          gap: 2,
          flexWrap: 'wrap',
        }}
      >
        <Box sx={{ minWidth: 0 }}>
          <Typography variant="h4" component="h1" sx={{ color: 'text.primary' }}>
            {title}
          </Typography>
          {description && (
            <Typography variant="body2" sx={{ color: 'text.secondary', mt: 0.5, maxWidth: '68ch' }}>
              {description}
            </Typography>
          )}
        </Box>
        {actions && (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexShrink: 0 }}>{actions}</Box>
        )}
      </Box>
      {below && <Box sx={{ mt: 2 }}>{below}</Box>}
    </Box>
  );
}
