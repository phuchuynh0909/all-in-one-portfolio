import React from 'react';
import {
  Container,
  Typography,
  Box,
  Button,
  CircularProgress,
  Snackbar,
  Alert,
  TextField,
  MenuItem,
} from '@mui/material';
import SyncIcon from '@mui/icons-material/Sync';
import { ReportTable } from '../components/report/ReportTable';
import {
  fetchReports,
  syncReports,
  fetchRagStatuses,
  triggerReportRag,
} from '../lib/services/report';
import type { Report as ReportType, SyncStats, RagStatus, PdfParser } from '../lib/services/report';

const RAG_IN_PROGRESS = ['PENDING', 'PARSING', 'EMBEDDING'];

const Report: React.FC = () => {
  const [reports, setReports] = React.useState<ReportType[]>([]);
  const [isLoading, setIsLoading] = React.useState(false);
  const [isSyncing, setIsSyncing] = React.useState(false);
  const [syncResult, setSyncResult] = React.useState<{ success: boolean; stats?: SyncStats; error?: string } | null>(null);
  const [ragStatuses, setRagStatuses] = React.useState<Record<number, RagStatus>>({});
  const [parser, setParser] = React.useState<PdfParser>('marker');

  const loadReports = async (symbol?: string) => {
    try {
      setIsLoading(true);
      const data = await fetchReports(symbol);
      setReports(data);
    } catch (error) {
      console.error('Error loading reports:', error);
    } finally {
      setIsLoading(false);
    }
  };

  const loadRagStatuses = React.useCallback(async () => {
    try {
      setRagStatuses(await fetchRagStatuses());
    } catch (error) {
      console.error('Error loading RAG statuses:', error);
    }
  }, []);

  const handleEmbed = async (reportId: number) => {
    // Optimistic: show queued immediately, then let polling take over.
    setRagStatuses((prev) => ({
      ...prev,
      [reportId]: { report_id: reportId, status: 'PENDING' },
    }));
    try {
      await triggerReportRag(reportId, false, parser);
    } catch (error) {
      console.error('Error triggering RAG pipeline:', error);
      setRagStatuses((prev) => ({
        ...prev,
        [reportId]: { report_id: reportId, status: 'FAILED', error: 'Failed to queue' },
      }));
    }
    loadRagStatuses();
  };

  const handleSync = async () => {
    try {
      setIsSyncing(true);
      setSyncResult(null);
      const response = await syncReports(100);
      setSyncResult({ success: true, stats: response.stats });
      // Reload reports after sync
      await loadReports();
    } catch (error) {
      console.error('Error syncing reports:', error);
      setSyncResult({ success: false, error: 'Failed to sync reports' });
    } finally {
      setIsSyncing(false);
    }
  };

  React.useEffect(() => {
    loadReports();
    loadRagStatuses();
  }, [loadRagStatuses]);

  // Poll RAG statuses while any job is in progress.
  React.useEffect(() => {
    const anyRunning = Object.values(ragStatuses).some((s) =>
      RAG_IN_PROGRESS.includes(s.status),
    );
    if (!anyRunning) return;
    const id = window.setInterval(loadRagStatuses, 4000);
    return () => window.clearInterval(id);
  }, [ragStatuses, loadRagStatuses]);

  return (
    <Container maxWidth="xl">
      <Box sx={{ py: 3 }}>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
          <Typography variant="h4" component="h1">
            Research Reports
          </Typography>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
            <TextField
              select
              size="small"
              label="Parser"
              value={parser}
              onChange={(e) => setParser(e.target.value as PdfParser)}
              sx={{ width: 150 }}
              helperText="For ✨ Embed"
            >
              <MenuItem value="marker">marker (local)</MenuItem>
              <MenuItem value="llamaparse">LlamaParse (cloud)</MenuItem>
            </TextField>
            <Button
              variant="outlined"
              startIcon={isSyncing ? <CircularProgress size={20} /> : <SyncIcon />}
              onClick={handleSync}
              disabled={isSyncing}
            >
              {isSyncing ? 'Syncing...' : 'Sync Latest'}
            </Button>
          </Box>
        </Box>
        <ReportTable
          reports={reports}
          isLoading={isLoading}
          onSymbolSearch={(symbol) => loadReports(symbol)}
          ragStatuses={ragStatuses}
          onEmbed={handleEmbed}
        />
      </Box>

      <Snackbar
        open={syncResult !== null}
        autoHideDuration={6000}
        onClose={() => setSyncResult(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
      >
        <Alert
          onClose={() => setSyncResult(null)}
          severity={syncResult?.success ? 'success' : 'error'}
          sx={{ width: '100%' }}
        >
          {syncResult?.success
            ? `Synced ${syncResult.stats?.created} new reports (${syncResult.stats?.missing} missing, ${syncResult.stats?.failed} failed)`
            : syncResult?.error}
        </Alert>
      </Snackbar>
    </Container>
  );
};

export default Report;