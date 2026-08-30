import React from 'react';
import { PageContainer, Panel, LoadingState, EmptyState } from '../components/ui';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Typography,
  Box,
  Paper,
  TextField,
  Button,
  CircularProgress,
  Alert,
  Link,
  Divider,
  Stack,
  Chip,
} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import SaveIcon from '@mui/icons-material/Save';
import OpenInNewIcon from '@mui/icons-material/OpenInNew';
import { fetchReportById, updateReportSummary } from '../lib/services/report';
import type { ReportDetail as ReportDetailType } from '../lib/services/report';
import { format } from 'date-fns';

const ReportDetail: React.FC = () => {
  const { reportId } = useParams<{ reportId: string }>();
  const navigate = useNavigate();
  
  const [report, setReport] = React.useState<ReportDetailType | null>(null);
  const [summary, setSummary] = React.useState('');
  const [isLoading, setIsLoading] = React.useState(true);
  const [isSaving, setIsSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [successMessage, setSuccessMessage] = React.useState<string | null>(null);

  React.useEffect(() => {
    const loadReport = async () => {
      if (!reportId) return;
      
      try {
        setIsLoading(true);
        setError(null);
        const data = await fetchReportById(parseInt(reportId, 10));
        setReport(data);
        setSummary(data.llm_summary || '');
      } catch (err) {
        setError('Failed to load report');
        console.error(err);
      } finally {
        setIsLoading(false);
      }
    };

    loadReport();
  }, [reportId]);

  const handleSave = async () => {
    if (!reportId) return;

    try {
      setIsSaving(true);
      setError(null);
      setSuccessMessage(null);
      await updateReportSummary(parseInt(reportId, 10), summary);
      setSuccessMessage('Summary saved successfully!');
      // Update local state
      if (report) {
        setReport({ ...report, llm_summary: summary });
      }
    } catch (err) {
      setError('Failed to save summary');
      console.error(err);
    } finally {
      setIsSaving(false);
    }
  };

  const handleBack = () => {
    navigate('/report');
  };

  if (isLoading) {
    return (
      <PageContainer maxWidth="1100px">
        <LoadingState label="Loading report" />
      </PageContainer>
    );
  }

  if (!report) {
    return (
      <PageContainer maxWidth="1100px">
        <EmptyState
          title="Report not found"
          description="It may have been removed, or the link is out of date."
          action={
            <Button startIcon={<ArrowBackIcon />} onClick={handleBack} variant="outlined" size="small">
              Back to reports
            </Button>
          }
        />
      </PageContainer>
    );
  }

  return (
    <PageContainer maxWidth="1100px">
      <>
        <Button startIcon={<ArrowBackIcon />} onClick={handleBack} size="small" sx={{ mb: 2 }}>
          Back to reports
        </Button>

        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}

        {successMessage && (
          <Alert severity="success" sx={{ mb: 2 }}>
            {successMessage}
          </Alert>
        )}

        <Panel title={report.tenbaocao}>

          <Stack direction="row" spacing={1} sx={{ mb: 3, flexWrap: 'wrap', gap: 1 }}>
            <Chip label={report.mack || 'N/A'} color="primary" />
            <Chip label={report.nguon} variant="outlined" />
            {report.rsnganh && <Chip label={report.rsnganh} variant="outlined" />}
            {report.ngaykn && (
              <Chip 
                label={format(new Date(report.ngaykn), 'dd/MM/yyyy')} 
                variant="outlined" 
              />
            )}
            {report.recommendation && (
              <Chip 
                label={report.recommendation} 
                color="success"
                variant="filled"
              />
            )}
            {report.report_category && (
              <Chip label={report.report_category} variant="outlined" color="info" />
            )}
            {report.status && (
              <Chip 
                label={report.status} 
                size="small"
                color={report.status === 'processed' ? 'success' : 'default'}
              />
            )}
          </Stack>

          <Box sx={{ mb: 3 }}>
            <Link 
              href={report.url} 
              target="_blank" 
              rel="noopener noreferrer"
              sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}
            >
              Open Original Report <OpenInNewIcon fontSize="small" />
            </Link>
          </Box>

          <Divider sx={{ my: 3 }} />

          {report.clean_content && (
            <>
              <Typography variant="h6" gutterBottom sx={{ fontWeight: 500 }}>
                Report Content
                {report.token_count && (
                  <Typography component="span" variant="caption" sx={{ ml: 1, color: 'text.secondary' }}>
                    ({report.token_count.toLocaleString()} tokens)
                  </Typography>
                )}
              </Typography>
              
              <Paper 
                variant="outlined" 
                sx={{ 
                  p: 2, 
                  mb: 3, 
                  maxHeight: '400px', 
                  overflow: 'auto',
                  backgroundColor: 'background.default',
                }}
              >
                <Typography 
                  variant="body2" 
                  sx={{ 
                    whiteSpace: 'pre-wrap',
                    fontFamily: 'inherit',
                    lineHeight: 1.8,
                  }}
                >
                  {report.clean_content}
                </Typography>
              </Paper>

              <Divider sx={{ my: 3 }} />
            </>
          )}

          <Typography variant="h6" gutterBottom sx={{ fontWeight: 500, color: 'primary.main' }}>
            Summary
          </Typography>
          
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Edit the summary - key points, investment thesis, target prices, risks, etc.
          </Typography>

          <TextField
            multiline
            rows={12}
            fullWidth
            placeholder="Write your summary here... Key points, investment thesis, target prices, risks, etc."
            value={summary}
            onChange={(e) => setSummary(e.target.value)}
            sx={{ 
              mb: 3,
              '& .MuiOutlinedInput-root': {
                fontFamily: 'inherit',
              }
            }}
          />

          <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
            <Button
              variant="contained"
              color="primary"
              startIcon={isSaving ? <CircularProgress size={20} color="inherit" /> : <SaveIcon />}
              onClick={handleSave}
              disabled={isSaving}
              size="large"
            >
              {isSaving ? 'Saving...' : 'Save Summary'}
            </Button>
          </Box>
        </Panel>
      </>
    </PageContainer>
  );
};

export default ReportDetail;

