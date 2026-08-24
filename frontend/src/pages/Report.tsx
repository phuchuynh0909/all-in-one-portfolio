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
import AddIcon from '@mui/icons-material/Add';
import { ReportTable } from '../components/report/ReportTable';
import { AddReportDialog } from '../components/report/AddReportDialog';
import {
  fetchReports,
  syncReports,
  fetchRagStatuses,
  triggerReportRag,
} from '../lib/services/report';
import type { Report as ReportType, RagStatus, PdfParser } from '../lib/services/report';

const RAG_IN_PROGRESS = ['PENDING', 'PARSING', 'EMBEDDING'];

type Notice = { severity: 'success' | 'error'; message: string };

const Report: React.FC = () => {
  const [reports, setReports] = React.useState<ReportType[]>([]);
  const [isLoading, setIsLoading] = React.useState(false);
  const [isSyncing, setIsSyncing] = React.useState(false);
  const [notice, setNotice] = React.useState<Notice | null>(null);
  const [ragStatuses, setRagStatuses] = React.useState<Record<number, RagStatus>>({});
  const [parser, setParser] = React.useState<PdfParser>('pymupdf4llm');
  const [isAddOpen, setIsAddOpen] = React.useState(false);

  const loadReports = async (symbol?: string, refresh = false) => {
    try {
      setIsLoading(true);
      const data = await fetchReports(symbol, refresh);
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
      setNotice(null);
      const response = await syncReports(100);
      const stats = response.stats;
      setNotice({
        severity: 'success',
        message: `Synced ${stats.created} new reports (${stats.missing} missing, ${stats.failed} failed)`,
      });
      // Reload reports after sync
      await loadReports();
    } catch (error) {
      console.error('Error syncing reports:', error);
      setNotice({ severity: 'error', message: 'Failed to sync reports' });
    } finally {
      setIsSyncing(false);
    }
  };

  const handleReportCreated = async (report: ReportType) => {
    setNotice({
      severity: 'success',
      message: `Added report #${report.id}${report.mack ? ` (${report.mack})` : ''}`,
    });
    // The list endpoint is cached server-side, so force a recompute.
    await loadReports(undefined, true);
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
              sx={{ width: 180 }}
              helperText="For ✨ Embed"
            >
              <MenuItem value="marker">marker (local)</MenuItem>
              <MenuItem value="llamaparse">LlamaParse (cloud)</MenuItem>
              <MenuItem value="docling">Docling (local)</MenuItem>
              <MenuItem value="pymupdf4llm">PyMuPDF4LLM (fast)</MenuItem>
            </TextField>
            <Button
              variant="contained"
              startIcon={<AddIcon />}
              onClick={() => setIsAddOpen(true)}
            >
              Add Report
            </Button>
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

      <AddReportDialog
        open={isAddOpen}
        onClose={() => setIsAddOpen(false)}
        onCreated={handleReportCreated}
      />

      <Snackbar
        open={notice !== null}
        autoHideDuration={6000}
        onClose={() => setNotice(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
      >
        <Alert
          onClose={() => setNotice(null)}
          severity={notice?.severity ?? 'success'}
          sx={{ width: '100%' }}
        >
          {notice?.message}
        </Alert>
      </Snackbar>
    </Container>
  );
};

export default Report;
