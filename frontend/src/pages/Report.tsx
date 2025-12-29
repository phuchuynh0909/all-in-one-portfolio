import React from 'react';
import { Container, Typography, Box, Button, CircularProgress, Snackbar, Alert } from '@mui/material';
import SyncIcon from '@mui/icons-material/Sync';
import { ReportTable } from '../components/report/ReportTable';
import { fetchReports, syncReports } from '../lib/services/report';
import type { Report as ReportType, SyncStats } from '../lib/services/report';

const Report: React.FC = () => {
  const [reports, setReports] = React.useState<ReportType[]>([]);
  const [isLoading, setIsLoading] = React.useState(false);
  const [isSyncing, setIsSyncing] = React.useState(false);
  const [syncResult, setSyncResult] = React.useState<{ success: boolean; stats?: SyncStats; error?: string } | null>(null);

  const loadReports = async (symbol?: string) => {
    try {
      setIsLoading(true);
      const data = await fetchReports(symbol);
      console.log(data);
      setReports(data);
    } catch (error) {
      console.error('Error loading reports:', error);
    } finally {
      setIsLoading(false);
    }
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
  }, []);

  return (
    <Container maxWidth="xl">
      <Box sx={{ py: 3 }}>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
          <Typography variant="h4" component="h1">
            Research Reports
          </Typography>
          <Button
            variant="outlined"
            startIcon={isSyncing ? <CircularProgress size={20} /> : <SyncIcon />}
            onClick={handleSync}
            disabled={isSyncing}
          >
            {isSyncing ? 'Syncing...' : 'Sync Latest'}
          </Button>
        </Box>
        <ReportTable
          reports={reports}
          isLoading={isLoading}
          onSymbolSearch={(symbol) => loadReports(symbol)}
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