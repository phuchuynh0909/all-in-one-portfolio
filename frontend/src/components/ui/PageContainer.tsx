import type { ReactNode } from 'react';
import { Box, type SxProps, type Theme } from '@mui/material';
import { layout } from '../../theme';

/** Standard page padding and max width. Every routed page starts with one. */
export default function PageContainer({
  children,
  maxWidth = layout.contentMaxWidth,
  sx,
}: {
  children: ReactNode;
  maxWidth?: string | number;
  sx?: SxProps<Theme>;
}) {
  return (
    <Box
      sx={{
        width: '100%',
        maxWidth,
        mx: 'auto',
        px: { xs: 2, md: 3 },
        py: { xs: 2, md: 3 },
        minWidth: 0,
        ...sx,
      }}
    >
      {children}
    </Box>
  );
}
