import { useEffect, useState } from 'react';
import { Card, CardContent, Typography, Grid, Box } from '@mui/material';
import { apiGet } from '../../lib/api';

type PortfolioSummary = {
  total_value: number;
  total_invested: number;
  total_profit_loss: number;
  total_profit_loss_pct: number;
  positions: Array<{
    id: number;
    ticker: string;
    quantity: number;
    purchase_price: number;
    purchase_date: string;
  }>;
};

export default function PortfolioSummary() {
  const [summary, setSummary] = useState<PortfolioSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiGet<PortfolioSummary>('/portfolio/summary')
      .then(setSummary)
      .catch((e) => setError(e.message));
  }, []);

  if (error) return <Typography color="error">{error}</Typography>;
  if (!summary) return <Typography>Loading...</Typography>;

  const formatCurrency = (value: number) => {
    return new Intl.NumberFormat('vi-VN', {
      style: 'currency',
      currency: 'VND',
      minimumFractionDigits: 0,
      maximumFractionDigits: 0
    }).format(value);
  };

  const formatPercent = (value: number) => {
    return new Intl.NumberFormat('vi-VN', {
      style: 'percent',
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    }).format(value / 100);
  };

  return (
    <Box sx={{ mb: 4 }}>
      <Grid container spacing={2}>
        <Grid item xs={12} md={3}>
          <Card>
            <CardContent>
              <Typography variant="subtitle2" color="text.secondary">
                Total Value
              </Typography>
              <Typography variant="h6">
                {formatCurrency(summary.total_value)}
              </Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} md={3}>
          <Card>
            <CardContent>
              <Typography variant="subtitle2" color="text.secondary">
                Total Invested
              </Typography>
              <Typography variant="h6">
                {formatCurrency(summary.total_invested)}
              </Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} md={3}>
          <Card>
            <CardContent>
              <Typography variant="subtitle2" color="text.secondary">
                Profit/Loss
              </Typography>
              <Typography
                variant="h6"
                color={summary.total_profit_loss >= 0 ? 'success.main' : 'error.main'}
              >
                {formatCurrency(summary.total_profit_loss)}
              </Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} md={3}>
          <Card>
            <CardContent>
              <Typography variant="subtitle2" color="text.secondary">
                Return %
              </Typography>
              <Typography
                variant="h6"
                color={summary.total_profit_loss_pct >= 0 ? 'success.main' : 'error.main'}
              >
                {formatPercent(summary.total_profit_loss_pct)}
              </Typography>
            </CardContent>
          </Card>
        </Grid>
      </Grid>
    </Box>
  );
}
