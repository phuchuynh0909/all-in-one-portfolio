import React, { useState, useEffect } from 'react';
import {
  Box,
  Typography,
  Card,
  CardContent,
  Button,
  Autocomplete,
  TextField,
  Alert,
  CircularProgress,
  Chip,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Grid,
  LinearProgress
} from '@mui/material';
import { 
  CloudDownload as CrawlIcon,
  CheckCircle as CompletedIcon,
  Schedule as PendingIcon
} from '@mui/icons-material';
import { crawlerApi } from '../../lib/services/crawler';
import type { CrawlableSymbol, CrawlStatus } from '../../lib/services/crawler';
interface DataCrawlerProps {
  onDataCrawled?: (symbol: string) => void;
}

export const DataCrawler: React.FC<DataCrawlerProps> = ({ onDataCrawled }) => {
  const [symbols, setSymbols] = useState<CrawlableSymbol[]>([]);
  const [selectedSymbol, setSelectedSymbol] = useState<CrawlableSymbol | null>(null);
  const [loading, setLoading] = useState(false);
  const [crawling, setCrawling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [crawlStatus, setCrawlStatus] = useState<CrawlStatus | null>(null);

  useEffect(() => {
    fetchAvailableSymbols();
  }, []);

  const fetchAvailableSymbols = async () => {
    setLoading(true);
    setError(null);
    
    try {
      const symbolsData = await crawlerApi.getAvailableSymbols();
      setSymbols(symbolsData);
    } catch (err) {
      setError('Failed to load available symbols: ' + (err instanceof Error ? err.message : 'Unknown error'));
    } finally {
      setLoading(false);
    }
  };

  const handleCrawlData = async () => {
    if (!selectedSymbol) return;

    setCrawling(true);
    setError(null);
    setSuccess(null);

    try {
      const response = await crawlerApi.crawlSymbol(selectedSymbol.ticker);
      
      if (response.status === 'started') {
        setSuccess(`Started crawling data for ${selectedSymbol.ticker}. This may take a few minutes.`);
        
        // Poll for status updates
        startStatusPolling(selectedSymbol.ticker);
      } else if (response.status === 'skipped') {
        setSuccess(response.message);
      }

      // Refresh the symbols list to update counts
      await fetchAvailableSymbols();
      
      if (onDataCrawled) {
        onDataCrawled(selectedSymbol.ticker);
      }

    } catch (err) {
      setError('Failed to crawl data: ' + (err instanceof Error ? err.message : 'Unknown error'));
    } finally {
      setCrawling(false);
    }
  };

  const startStatusPolling = (symbol: string) => {
    const pollInterval = setInterval(async () => {
      try {
        const status = await crawlerApi.getCrawlStatus(symbol);
        setCrawlStatus(status);
        
        if (status.has_data) {
          clearInterval(pollInterval);
          setSuccess(`Successfully imported ${status.data_count} data points for ${symbol}`);
          await fetchAvailableSymbols();
        }
      } catch (err) {
        console.error('Error polling status:', err);
        clearInterval(pollInterval);
      }
    }, 3000); // Poll every 3 seconds

    // Stop polling after 5 minutes
    setTimeout(() => clearInterval(pollInterval), 300000);
  };

  const openCrawlDialog = () => {
    if (selectedSymbol) {
      setDialogOpen(true);
    }
  };

  const needsCrawlingSymbols = symbols.filter(s => s.needs_crawling);
  const hasDataSymbols = symbols.filter(s => !s.needs_crawling);

  return (
    <Card sx={{ mb: 3 }}>
      <CardContent>
        <Box sx={{ display: 'flex', alignItems: 'center', mb: 2 }}>
          <CrawlIcon sx={{ mr: 1, color: 'primary.main' }} />
          <Typography variant="h6">
            Data Crawler
          </Typography>
        </Box>

        <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
          Crawl financial data from external sources for companies that don't have data yet.
        </Typography>

        {/* Symbol Selection */}
        <Box sx={{ display: 'flex', gap: 2, mb: 3, alignItems: 'center' }}>
          <Autocomplete
            sx={{ minWidth: 300 }}
            value={selectedSymbol}
            onChange={(_, newValue) => setSelectedSymbol(newValue)}
            options={symbols}
            getOptionLabel={(option) => `${option.ticker} - ${option.name}`}
            loading={loading}
            disabled={loading || crawling}
            groupBy={(option) => option.needs_crawling ? 'Needs Crawling' : 'Has Data'}
            renderInput={(params) => (
              <TextField
                {...params}
                label="Select Symbol"
                placeholder="Choose a company symbol..."
                InputProps={{
                  ...params.InputProps,
                  endAdornment: (
                    <>
                      {loading ? <CircularProgress color="inherit" size={20} /> : null}
                      {params.InputProps.endAdornment}
                    </>
                  ),
                }}
              />
            )}
            renderOption={(props, option) => (
              <Box component="li" {...props}>
                <Box sx={{ display: 'flex', justifyContent: 'space-between', width: '100%', alignItems: 'center' }}>
                  <Box>
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>
                      {option.ticker} - {option.name}
                    </Typography>
                  </Box>
                  <Box sx={{ display: 'flex', gap: 1 }}>
                    {option.needs_crawling ? (
                      <Chip 
                        icon={<PendingIcon />}
                        label="Needs Data" 
                        size="small" 
                        color="warning"
                        variant="outlined"
                      />
                    ) : (
                      <Chip 
                        icon={<CompletedIcon />}
                        label={`${option.data_count} points`} 
                        size="small" 
                        color="success"
                        variant="outlined"
                      />
                    )}
                  </Box>
                </Box>
              </Box>
            )}
            noOptionsText={loading ? "Loading..." : "No symbols available"}
          />

          <Button
            variant="contained"
            onClick={openCrawlDialog}
            disabled={!selectedSymbol || crawling || !selectedSymbol.needs_crawling}
            startIcon={crawling ? <CircularProgress size={20} /> : <CrawlIcon />}
          >
            {crawling ? 'Crawling...' : 'Crawl Data'}
          </Button>
        </Box>

        {/* Statistics */}
        <Grid container spacing={2} sx={{ mb: 2 }}>
          <Grid item xs={6}>
            <Box sx={{ textAlign: 'center', p: 2, bgcolor: 'warning.light', borderRadius: 1 }}>
              <Typography variant="h4" color="warning.contrastText">
                {needsCrawlingSymbols.length}
              </Typography>
              <Typography variant="body2" color="warning.contrastText">
                Companies need data
              </Typography>
            </Box>
          </Grid>
          <Grid item xs={6}>
            <Box sx={{ textAlign: 'center', p: 2, bgcolor: 'success.light', borderRadius: 1 }}>
              <Typography variant="h4" color="success.contrastText">
                {hasDataSymbols.length}
              </Typography>
              <Typography variant="body2" color="success.contrastText">
                Companies have data
              </Typography>
            </Box>
          </Grid>
        </Grid>

        {/* Status Messages */}
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}

        {success && (
          <Alert severity="success" sx={{ mb: 2 }}>
            {success}
          </Alert>
        )}

        {crawlStatus && crawling && (
          <Box sx={{ mb: 2 }}>
            <Typography variant="body2" gutterBottom>
              Crawling in progress for {crawlStatus.symbol}...
            </Typography>
            <LinearProgress />
          </Box>
        )}

        {/* Crawl Confirmation Dialog */}
        <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)}>
          <DialogTitle>
            Confirm Data Crawling
          </DialogTitle>
          <DialogContent>
            <Typography variant="body1" gutterBottom>
              Are you sure you want to crawl financial data for <strong>{selectedSymbol?.ticker}</strong>?
            </Typography>
            <Typography variant="body2" color="text.secondary">
              This process will:
              <br />• Fetch financial data from external sources
              <br />• Import the data into your database
              <br />• Make the company available for analysis
              <br />• Take several minutes to complete
            </Typography>
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setDialogOpen(false)}>
              Cancel
            </Button>
            <Button 
              onClick={() => {
                setDialogOpen(false);
                handleCrawlData();
              }}
              variant="contained"
              disabled={crawling}
            >
              Start Crawling
            </Button>
          </DialogActions>
        </Dialog>
      </CardContent>
    </Card>
  );
};
