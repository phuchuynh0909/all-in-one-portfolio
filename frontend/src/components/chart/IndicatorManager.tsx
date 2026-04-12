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
        background: 'linear-gradient(135deg, rgba(18,18,28,0.97) 0%, rgba(20,20,32,0.97) 100%)',
        border: '1px solid rgba(99,102,241,0.3)',
        borderRadius: 2,
        p: 1.5,
        minWidth: 230,
        backdropFilter: 'blur(8px)',
        boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
      }}
    >
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 0.75 }}>
        <Typography
          variant="caption"
          sx={{ color: '#9ca3af', fontWeight: 700, letterSpacing: 0.8, textTransform: 'uppercase', fontSize: '0.68rem' }}
        >
          Indicators
        </Typography>
        <IconButton size="small" onClick={onClose} sx={{ color: '#6b7280', p: 0.25 }}>
          <Close sx={{ fontSize: 14 }} />
        </IconButton>
      </Stack>

      <Divider sx={{ borderColor: 'rgba(99,102,241,0.15)', mb: 0.75 }} />

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
              '&:hover': { bgcolor: 'rgba(99,102,241,0.07)' },
              transition: 'background 0.15s',
            }}
          >
            <IconButton
              size="small"
              onClick={() => onToggleVisible(cfg.id)}
              sx={{ color: cfg.visible ? '#6366f1' : '#374151', p: 0.25, flexShrink: 0 }}
            >
              {cfg.visible
                ? <VisibilityOutlined sx={{ fontSize: 15 }} />
                : <VisibilityOffOutlined sx={{ fontSize: 15 }} />}
            </IconButton>

            <Typography
              variant="caption"
              sx={{
                flex: 1,
                color: cfg.visible ? '#d1d5db' : '#4b5563',
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
                  sx={{ color: '#4b5563', p: 0.25, flexShrink: 0, '&:hover': { color: '#a5b4fc' } }}
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
            background: 'linear-gradient(135deg, rgba(18,18,28,0.99) 0%, rgba(22,22,36,0.99) 100%)',
            border: '1px solid rgba(99,102,241,0.35)',
            borderRadius: 2,
          },
        }}
      >
        <DialogTitle sx={{ color: '#e2e8f0', fontSize: '0.9rem', fontWeight: 600, pb: 0.5 }}>
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
                  '& .MuiInputBase-root': { color: '#e2e8f0', bgcolor: 'rgba(15,15,25,0.8)' },
                  '& .MuiOutlinedInput-notchedOutline': { borderColor: 'rgba(99,102,241,0.3)' },
                  '&:hover .MuiOutlinedInput-notchedOutline': { borderColor: 'rgba(99,102,241,0.5)' },
                  '& .Mui-focused .MuiOutlinedInput-notchedOutline': { borderColor: '#6366f1' },
                  '& .MuiInputLabel-root': { color: '#9ca3af' },
                  '& .MuiInputLabel-root.Mui-focused': { color: '#a5b4fc' },
                }}
              />
            ))}
          </Stack>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2, gap: 1 }}>
          <Button size="small" onClick={() => setSettingsFor(null)} sx={{ color: '#6b7280', minWidth: 64 }}>
            Cancel
          </Button>
          <Button
            size="small"
            variant="contained"
            onClick={handleApply}
            sx={{ bgcolor: '#6366f1', '&:hover': { bgcolor: '#4f46e5' }, minWidth: 64 }}
          >
            Apply
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
