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
        bgcolor: 'surface.default',
        border: 1,
        borderColor: 'line.subtle',
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
                color: item.active ? 'primary.main' : 'text.secondary',
                bgcolor: item.active ? 'action.selected' : 'transparent',
                '&:hover': {
                  bgcolor: item.active ? 'action.selected' : 'action.hover',
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
