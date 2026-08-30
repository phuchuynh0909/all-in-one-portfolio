import { useCallback, useEffect, useState } from 'react';
import { Box, Button, Chip, Stack, Typography } from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import { apiGet, API_BASE_URL } from '../lib/api';
import { PageContainer, PageHeader, Panel, Numeric } from '../components/ui';

type Probe = {
  status: 'checking' | 'ok' | 'error';
  detail: string;
  /** Round-trip time in ms for the last successful probe. */
  latencyMs: number | null;
};

const INITIAL: Probe = { status: 'checking', detail: 'Contacting API…', latencyMs: null };

export default function Health() {
  const [probe, setProbe] = useState<Probe>(INITIAL);
  const [checkedAt, setCheckedAt] = useState<Date | null>(null);

  const check = useCallback(async () => {
    setProbe(INITIAL);
    const started = performance.now();
    try {
      const data = await apiGet<{ status: string }>('/health');
      setProbe({
        status: 'ok',
        detail: data.status,
        latencyMs: Math.round(performance.now() - started),
      });
    } catch (e) {
      setProbe({
        status: 'error',
        detail: e instanceof Error ? e.message : String(e),
        latencyMs: null,
      });
    } finally {
      setCheckedAt(new Date());
    }
  }, []);

  useEffect(() => {
    void check();
  }, [check]);

  const chipColor =
    probe.status === 'ok' ? 'success' : probe.status === 'error' ? 'error' : 'default';

  return (
    <PageContainer maxWidth="900px">
      <PageHeader
        title="Health"
        description="Connectivity to the backend API that every page on this terminal depends on."
        actions={
          <Button
            variant="outlined"
            size="small"
            startIcon={<RefreshIcon fontSize="small" />}
            onClick={() => void check()}
            disabled={probe.status === 'checking'}
          >
            Re-check
          </Button>
        }
      />

      <Panel title="API endpoint">
        <Stack spacing={2}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
            <Chip
              size="small"
              color={chipColor}
              label={probe.status === 'checking' ? 'Checking' : probe.status.toUpperCase()}
            />
            {probe.latencyMs != null && (
              <Numeric value={probe.latencyMs} decimals={0} sx={{ color: 'text.secondary' }} />
            )}
            {probe.latencyMs != null && (
              <Typography variant="caption" sx={{ color: 'text.tertiary' }}>
                ms
              </Typography>
            )}
          </Box>

          <Box>
            <Typography variant="overline2">Base URL</Typography>
            <Typography variant="mono" sx={{ wordBreak: 'break-all' }}>
              {API_BASE_URL}
            </Typography>
          </Box>

          <Box>
            <Typography variant="overline2">Response</Typography>
            <Box
              component="pre"
              sx={{
                m: 0,
                p: 1.5,
                bgcolor: 'surface.inset',
                border: 1,
                borderColor: 'line.subtle',
                borderRadius: 1,
                fontSize: '0.75rem',
                color: probe.status === 'error' ? 'error.main' : 'text.primary',
                overflowX: 'auto',
              }}
            >
              {probe.detail}
            </Box>
          </Box>

          {checkedAt && (
            <Typography variant="caption" sx={{ color: 'text.tertiary' }}>
              Last checked {checkedAt.toLocaleTimeString()}
            </Typography>
          )}
        </Stack>
      </Panel>
    </PageContainer>
  );
}
