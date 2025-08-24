import { useState } from 'react';
import { Box, TextField, Container, Paper, Dialog, DialogTitle, DialogContent, Typography, Link } from '@mui/material';
import type { Report } from '../lib/services/report';
import StockChart from '../components/chart/StockChart';

export default function ChartPage() {
  const [symbol, setSymbol] = useState('VNINDEX');
  const [currentSymbol, setCurrentSymbol] = useState('VNINDEX');
  const [isFocused, setIsFocused] = useState(false);
  const [selectedReport, setSelectedReport] = useState<Report | null>(null);

  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      setCurrentSymbol(symbol);
      setIsFocused(false);
    }
  };

  const handleFocus = () => {
    setIsFocused(true);
    setSymbol('');
  };

  const handleBlur = () => {
    setIsFocused(false);
    if (!symbol) {
      setSymbol(currentSymbol);
    }
  };

  const handleReportClick = (report: Report) => {
    setSelectedReport(report);
  };

  return (
    <Container maxWidth="xl">
      <Box sx={{ my: 4 }}>
        <Paper sx={{ p: 2, mb: 2 }}>
          <TextField
            label="Symbol"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            onKeyDown={handleKeyDown}
            onFocus={handleFocus}
            onBlur={handleBlur}
            variant="outlined"
            size="small"
            placeholder={isFocused ? "Enter symbol" : "Press Enter to update chart"}
          />
        </Paper>
        
        <Paper sx={{ p: 2 }}>
          <StockChart 
            symbol={currentSymbol} 
            onReportClick={handleReportClick}
          />
        </Paper>
      </Box>

      {/* Report Dialog */}
      <Dialog 
        open={!!selectedReport} 
        onClose={() => setSelectedReport(null)}
        maxWidth="md"
        fullWidth
      >
        {selectedReport && (
          <>
            <DialogTitle>Research Report</DialogTitle>
            <DialogContent>
              <Box sx={{ py: 2 }}>
                <Typography variant="h6" gutterBottom>
                  {selectedReport.tenbaocao}
                </Typography>
                <Typography variant="body1" gutterBottom>
                  Symbol: {selectedReport.mack}
                </Typography>
                <Typography variant="body1" gutterBottom>
                  Source: {selectedReport.nguon}
                </Typography>
                <Typography variant="body1" gutterBottom>
                  Date: {selectedReport.ngaykn ? new Date(selectedReport.ngaykn).toLocaleDateString() : 'N/A'}
                </Typography>
                <Typography variant="body1" gutterBottom>
                  Sector: {selectedReport.rsnganh || 'N/A'}
                </Typography>
                <Link href={selectedReport.url} target="_blank" rel="noopener noreferrer">
                  View Report
                </Link>
              </Box>
            </DialogContent>
          </>
        )}
      </Dialog>
    </Container>
  );
}
