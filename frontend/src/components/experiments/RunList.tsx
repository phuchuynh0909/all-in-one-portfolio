import { useMemo, useState } from 'react';
import {
  Checkbox, Chip, List, ListItemButton, ListItemText, Stack, TextField, Typography,
} from '@mui/material';
import type { RunMeta } from '../../lib/experiments/types';

interface Props {
  runs: RunMeta[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  comparedIds: string[];
  onToggleCompare: (id: string) => void;
}

export default function RunList({
  runs, selectedId, onSelect, comparedIds, onToggleCompare,
}: Props) {
  const [search, setSearch] = useState('');

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return runs;
    return runs.filter(
      (r) => r.name.toLowerCase().includes(q) || r.tags.some((t) => t.toLowerCase().includes(q)),
    );
  }, [runs, search]);

  return (
    <Stack spacing={1}>
      <TextField
        size="small" label="Search runs or tags" value={search}
        onChange={(e) => setSearch(e.target.value)}
      />
      <List dense disablePadding>
        {filtered.map((run) => (
          <ListItemButton
            key={run.run_id}
            selected={run.run_id === selectedId}
            onClick={() => onSelect(run.run_id)}
          >
            <Checkbox
              edge="start" size="small"
              checked={comparedIds.includes(run.run_id)}
              onClick={(e) => { e.stopPropagation(); onToggleCompare(run.run_id); }}
            />
            <ListItemText
              primary={run.name}
              secondary={
                <>
                  <Typography variant="caption" component="span">
                    {new Date(run.created_at).toLocaleDateString()} · {run.n_trades} trades
                  </Typography>
                  <Stack direction="row" spacing={0.5} sx={{ mt: 0.5, flexWrap: 'wrap' }}>
                    {run.tags.map((t) => <Chip key={t} label={t} size="small" />)}
                  </Stack>
                </>
              }
              secondaryTypographyProps={{ component: 'div' }}
            />
          </ListItemButton>
        ))}
      </List>
    </Stack>
  );
}
