/**
 * Vertical icon rail on the right edge of the chart.
 *
 * Stands in for the charting library's widget bar, which ships only with the
 * Trading Terminal package. Each item toggles one side panel; the active item is
 * highlighted so the rail shows what is currently open.
 */
import type { ReactNode } from 'react';
import { Box, IconButton, Stack, Tooltip } from '@mui/material';

export interface RailItem {
  id: string;
  /** Tooltip text. */
  label: string;
  icon: ReactNode;
  active: boolean;
  onClick: () => void;
}

export default function ChartSideRail({ items }: { items: RailItem[] }) {
  return (
    <Box
      sx={{
        width: 44,
        flexShrink: 0,
        py: 1,
        display: 'flex',
        justifyContent: 'center',
        background: 'linear-gradient(135deg, rgba(30, 30, 46, 0.9) 0%, rgba(30, 30, 40, 0.95) 100%)',
        border: '1px solid rgba(99, 102, 241, 0.2)',
        borderRadius: 2,
      }}
    >
      <Stack spacing={0.5} alignItems="center">
        {items.map((item) => (
          <Tooltip key={item.id} title={item.label} placement="left">
            <IconButton
              size="small"
              onClick={item.onClick}
              aria-label={item.label}
              aria-pressed={item.active}
              sx={{
                width: 32,
                height: 32,
                borderRadius: 1,
                color: item.active ? '#c7d2fe' : '#9ca3af',
                bgcolor: item.active ? 'rgba(99,102,241,0.22)' : 'transparent',
                '&:hover': {
                  bgcolor: item.active ? 'rgba(99,102,241,0.3)' : 'rgba(148,163,184,0.12)',
                },
              }}
            >
              {item.icon}
            </IconButton>
          </Tooltip>
        ))}
      </Stack>
    </Box>
  );
}
