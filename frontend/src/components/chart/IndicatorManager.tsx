import { useState } from 'react';
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  IconButton,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import { Close, Settings, VisibilityOffOutlined, VisibilityOutlined } from '@mui/icons-material';
import type { IndicatorConfig, ParamDef } from './StockChart';

interface Props {
  configs: IndicatorConfig[];
  onToggleVisible: (id: string) => void;
  onChangeParams: (id: string, params: Record<string, number>) => void;
  onClose: () => void;
}

export default function IndicatorManager({ configs, onToggleVisible, onChangeParams, onClose }: Props) {
  const [settingsFor, setSettingsFor] = useState<IndicatorConfig | null>(null);
  const [draftParams, setDraftParams] = useState<Record<string, string>>({});

  const openSettings = (cfg: IndicatorConfig) => {
    setSettingsFor(cfg);
    setDraftParams(Object.fromEntries(Object.entries(cfg.params).map(([k, v]) => [k, String(v)])));
  };

  const handleApply = () => {
    if (!settingsFor) return;
    const newParams: Record<string, number> = {};
    for (const def of settingsFor.paramDefs) {
      const val = parseFloat(draftParams[def.key]);
      newParams[def.key] = isNaN(val) ? settingsFor.params[def.key] : val;
    }
    onChangeParams(settingsFor.id, newParams);
    setSettingsFor(null);
  };

  return (
    <Box
      sx={{
        position: 'absolute',
        top: 52,
        right: 12,
        zIndex: 100,
        backgroundColor: 'var(--color-bg-surface-overlay)',
        border: '1px solid var(--color-border-default)',
        borderRadius: 2,
        p: 1.5,
        minWidth: 230,
        backdropFilter: 'blur(8px)',
        boxShadow: 'var(--shadow-lg)',
      }}
    >
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 0.75 }}>
        <Typography
          variant="caption"
          sx={{ color: 'var(--color-text-secondary)', fontWeight: 700, letterSpacing: 0.8, textTransform: 'uppercase', fontSize: '0.68rem' }}
        >
          Indicators
        </Typography>
        <IconButton size="small" onClick={onClose} sx={{ color: 'var(--color-text-tertiary)', p: 0.25 }}>
          <Close sx={{ fontSize: 14 }} />
        </IconButton>
      </Stack>

      <Divider sx={{ borderColor: 'var(--color-border-subtle)', mb: 0.75 }} />

      <Stack spacing={0}>
        {configs.map((cfg) => (
          <Stack
            key={cfg.id}
            direction="row"
            alignItems="center"
            spacing={0.5}
            sx={{
              px: 0.5,
              py: 0.35,
              borderRadius: 1,
              '&:hover': { bgcolor: 'action.hover' },
              transition: 'background 0.15s',
            }}
          >
            <IconButton
              size="small"
              onClick={() => onToggleVisible(cfg.id)}
              sx={{ color: cfg.visible ? 'var(--color-accent)' : 'var(--color-text-disabled)', p: 0.25, flexShrink: 0 }}
            >
              {cfg.visible
                ? <VisibilityOutlined sx={{ fontSize: 15 }} />
                : <VisibilityOffOutlined sx={{ fontSize: 15 }} />}
            </IconButton>

            <Typography
              variant="caption"
              sx={{
                flex: 1,
                color: cfg.visible ? 'var(--color-text-primary)' : 'var(--color-text-disabled)',
                fontSize: '0.8rem',
                userSelect: 'none',
                transition: 'color 0.15s',
              }}
            >
              {cfg.label}
            </Typography>

            {cfg.paramDefs.length > 0 && (
              <Tooltip title={Object.entries(cfg.params).map(([k, v]) => `${k}: ${v}`).join(', ')} placement="left">
                <IconButton
                  size="small"
                  onClick={() => openSettings(cfg)}
                  sx={{ color: 'var(--color-text-tertiary)', p: 0.25, flexShrink: 0, '&:hover': { color: 'var(--color-accent)' } }}
                >
                  <Settings sx={{ fontSize: 13 }} />
                </IconButton>
              </Tooltip>
            )}
          </Stack>
        ))}
      </Stack>

      {/* Settings dialog — renders in body portal, not clipped by chart */}
      <Dialog
        open={!!settingsFor}
        onClose={() => setSettingsFor(null)}
        maxWidth="xs"
        fullWidth
        PaperProps={{
          sx: {
            backgroundColor: 'var(--color-bg-surface-overlay)',
            border: '1px solid var(--color-border-default)',
            borderRadius: 2,
          },
        }}
      >
        <DialogTitle sx={{ color: 'var(--color-text-primary)', fontSize: '0.9rem', fontWeight: 600, pb: 0.5 }}>
          {settingsFor?.label} — Settings
        </DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            {settingsFor?.paramDefs.map((def: ParamDef) => (
              <TextField
                key={def.key}
                label={def.label}
                type="number"
                size="small"
                fullWidth
                value={draftParams[def.key] ?? ''}
                onChange={(e) => setDraftParams({ ...draftParams, [def.key]: e.target.value })}
                inputProps={{ min: def.min, max: def.max, step: def.step }}
                sx={{
                  '& .MuiInputBase-root': { color: 'var(--color-text-primary)', bgcolor: 'var(--color-bg-inset)' },
                  '& .MuiOutlinedInput-notchedOutline': { borderColor: 'var(--color-border-default)' },
                  '&:hover .MuiOutlinedInput-notchedOutline': { borderColor: 'var(--color-border-default)' },
                  '& .Mui-focused .MuiOutlinedInput-notchedOutline': { borderColor: 'var(--color-accent)' },
                  '& .MuiInputLabel-root': { color: 'var(--color-text-secondary)' },
                  '& .MuiInputLabel-root.Mui-focused': { color: 'var(--color-accent)' },
                }}
              />
            ))}
          </Stack>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2, gap: 1 }}>
          <Button size="small" onClick={() => setSettingsFor(null)} sx={{ color: 'var(--color-text-tertiary)', minWidth: 64 }}>
            Cancel
          </Button>
          <Button
            size="small"
            variant="contained"
            onClick={handleApply}
            sx={{ bgcolor: 'var(--color-accent)', color: 'var(--color-text-on-accent)', '&:hover': { bgcolor: 'var(--color-accent-hover)' }, minWidth: 64 }}
          >
            Apply
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
