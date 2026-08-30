import type { ReactNode } from 'react';
import { Box } from '@mui/material';

/**
 * Responsive grid of StatTiles. Auto-fits rather than using fixed breakpoints
 * so a row of 3 and a row of 7 both look deliberate.
 */
export default function StatRow({
  children,
  min = 170,
}: {
  children: ReactNode;
  /** Minimum tile width in px before wrapping. */
  min?: number;
}) {
  return (
    <Box
      sx={{
        display: 'grid',
        gap: 1.5,
        gridTemplateColumns: `repeat(auto-fit, minmax(${min}px, 1fr))`,
      }}
    >
      {children}
    </Box>
  );
}
