import { useState, useEffect, useMemo } from 'react';
import {
  Box,
  Typography,
  CircularProgress,
  Card,
  CardContent,
  TextField,
  Button,
  Chip,
  Stack,
  ToggleButtonGroup,
  ToggleButton,
} from '@mui/material';
import { LineChart } from '@mui/x-charts/LineChart';
import { DatePicker } from '@mui/x-date-pickers/DatePicker';
import { LocalizationProvider } from '@mui/x-date-pickers/LocalizationProvider';
import { AdapterDateFns } from '@mui/x-date-pickers/AdapterDateFns';
import { fetchTimeseries } from '../../lib/services/timeseries';

type TimePeriod = 'YTD' | '1M' | '3M' | '6M' | '1Y' | '3Y' | '5Y' | '10Y' | 'ALL' | 'CUSTOM';

interface StockData {
  symbol: string;
  timestamps: string[];
  data: number[];
  loading: boolean;
  error: string | null;
}

const PERIODS: { value: TimePeriod; label: string }[] = [
  { value: 'YTD', label: 'YTD' },
  { value: '1M', label: '1M' },
  { value: '3M', label: '3M' },
  { value: '6M', label: '6M' },
  { value: '1Y', label: '1Y' },
  { value: '3Y', label: '3Y' },
  { value: '5Y', label: '5Y' },
  { value: '10Y', label: '10Y' },
  { value: 'ALL', label: 'ALL' },
  { value: 'CUSTOM', label: 'Custom' },
];

const COLORS = [
  '#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd',
  '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf'
];

export default function StockComparisonChart() {
  const [stocksData, setStocksData] = useState<StockData[]>([]);
  const [newSymbol, setNewSymbol] = useState<string>('');
  const [period, setPeriod] = useState<TimePeriod>('6M');
  const [customStartDate, setCustomStartDate] = useState<Date | null>(null);
  const [dateRangeMessage, setDateRangeMessage] = useState<string>('');

  const getStartDateForPeriod = (period: TimePeriod): string => {
    const now = new Date();
    const year = now.getFullYear();
    
    switch (period) {
      case 'YTD':
        return `${year}-01-01`;
      case '1M':
        return new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000).toISOString().split('T')[0];
      case '3M':
        return new Date(now.getTime() - 90 * 24 * 60 * 60 * 1000).toISOString().split('T')[0];
      case '6M':
        return new Date(now.getTime() - 180 * 24 * 60 * 60 * 1000).toISOString().split('T')[0];
      case '1Y':
        return new Date(now.getTime() - 365 * 24 * 60 * 60 * 1000).toISOString().split('T')[0];
      case '3Y':
        return new Date(now.getTime() - 3 * 365 * 24 * 60 * 60 * 1000).toISOString().split('T')[0];
      case '5Y':
        return new Date(now.getTime() - 5 * 365 * 24 * 60 * 60 * 1000).toISOString().split('T')[0];
      case '10Y':
        return new Date(now.getTime() - 10 * 365 * 24 * 60 * 60 * 1000).toISOString().split('T')[0];
      case 'ALL':
        return '2020-01-01';
      default:
        return new Date(now.getTime() - 365 * 24 * 60 * 60 * 1000).toISOString().split('T')[0];
    }
  };

  const filterDataByPeriod = (timestamps: string[], data: number[], period: TimePeriod, customDate?: Date | null) => {
    if (period === 'ALL') {
      return { timestamps, data };
    }

    let startDate: string;
    
    if (period === 'CUSTOM') {
      if (customDate) {
        startDate = customDate.toISOString().split('T')[0];
      } else {
        // If CUSTOM is selected but no date provided, use 1 year ago
        const fallbackDate = new Date(Date.now() - 365 * 24 * 60 * 60 * 1000);
        startDate = fallbackDate.toISOString().split('T')[0];
      }
    } else {
      startDate = getStartDateForPeriod(period);
    }
    
    const startTime = new Date(startDate).getTime();
    
    // Find the index where data should start
    const startIndex = timestamps.findIndex(timestamp => {
      return new Date(timestamp).getTime() >= startTime;
    });

    if (startIndex === -1) {
      // If no data found for the period, return full dataset
      return { timestamps, data };
    }
    
    // Check if requested date is before all available data
    if (startIndex === 0 && new Date(timestamps[0]).getTime() > startTime) {
      // Requested date is before all available data, return full dataset
      return { timestamps, data };
    }

    // Filter data
    const filteredTimestamps = timestamps.slice(startIndex);
    const filteredData = data.slice(startIndex);

    return { 
      timestamps: filteredTimestamps, 
      data: filteredData 
    };
  };

  const addStock = async () => {
    const symbol = newSymbol.trim().toUpperCase();
    if (!symbol || stocksData.some(stock => stock.symbol === symbol)) {
      return;
    }

    // Add placeholder for loading state
    const newStock: StockData = {
      symbol,
      timestamps: [],
      data: [],
      loading: true,
      error: null,
    };

    setStocksData(prev => [...prev, newStock]);
    setNewSymbol('');

    // Fetch data
    try {
      const result = await fetchTimeseries(symbol, {
        start_date: "2020-01-01",
        end_date: new Date().toISOString().split('T')[0],
        interval: "1d"
      });

      setStocksData(prev => prev.map(stock => 
        stock.symbol === symbol 
          ? {
              ...stock,
              timestamps: result.timestamps,
              data: result.timeseries.close,
              loading: false,
            }
          : stock
      ));
    } catch (error) {
      setStocksData(prev => prev.map(stock => 
        stock.symbol === symbol 
          ? {
              ...stock,
              loading: false,
              error: error instanceof Error ? error.message : 'Failed to load data',
            }
          : stock
      ));
    }
  };

  const removeStock = (symbol: string) => {
    setStocksData(prev => prev.filter(stock => stock.symbol !== symbol));
  };

  // Handle custom start date and period changes
  useEffect(() => {
    if (period === 'CUSTOM' && !customStartDate) {
      // Set default custom date to 1 year ago when switching to CUSTOM
      const defaultDate = new Date(Date.now() - 365 * 24 * 60 * 60 * 1000);
      setCustomStartDate(defaultDate);
    }
  }, [period, customStartDate]);

  // Memoized chart data with date range validation
  const chartData = useMemo(() => {
    if (stocksData.length === 0) return null;

    // Check date range for CUSTOM period and set message
    if (period === 'CUSTOM' && customStartDate && stocksData.length > 0) {
      const firstStockWithData = stocksData.find(stock => stock.timestamps.length > 0);
      if (firstStockWithData) {
        const requestedTime = customStartDate.getTime();
        const firstDataTime = new Date(firstStockWithData.timestamps[0]).getTime();
        const lastDataTime = new Date(firstStockWithData.timestamps[firstStockWithData.timestamps.length - 1]).getTime();
        
        if (requestedTime < firstDataTime) {
          const firstDataDate = new Date(firstStockWithData.timestamps[0]).toLocaleDateString();
          setDateRangeMessage(`⚠️ Selected date is before available data. Showing data from ${firstDataDate} onwards.`);
        } else if (requestedTime > lastDataTime) {
          setDateRangeMessage(`⚠️ Selected date is after available data. Showing all available data.`);
        } else {
          setDateRangeMessage('');
        }
      }
    } else {
      setDateRangeMessage('');
    }

    // Find common timestamps (intersection of all stocks)
    const loadedStocks = stocksData.filter(stock => !stock.loading && !stock.error && stock.timestamps.length > 0);
    if (loadedStocks.length === 0) return null;

    // Use first stock's timestamps as base and filter others to match
    const baseStock = loadedStocks[0];
    const { timestamps: filteredTimestamps } = filterDataByPeriod(baseStock.timestamps, baseStock.data, period, customStartDate);

    const series = loadedStocks.map((stock, index) => {
      const { data: filteredData } = filterDataByPeriod(stock.timestamps, stock.data, period, customStartDate);
      
      if (filteredData.length === 0) {
        return {
          data: [],
          label: stock.symbol,
          color: COLORS[index % COLORS.length],
          showMark: false,
        };
      }

      // Normalize to percentage returns (like sectors)
      const initialValue = filteredData[0];
      const normalizedData = filteredData.map((value: number) => {
        if (initialValue !== 0 && initialValue !== undefined && value !== undefined) {
          return ((value - initialValue) / initialValue) * 100;
        }
        return 0;
      });

      return {
        data: normalizedData,
        label: stock.symbol,
        color: COLORS[index % COLORS.length],
        showMark: false,
      };
    });

    return {
      timestamps: filteredTimestamps,
      series: series.filter(s => s.data.length > 0),
    };
  }, [stocksData, period, customStartDate]);

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      addStock();
    }
  };

  return (
    <Card sx={{ mt: 3 }}>
      <CardContent>
        <Typography variant="h6" gutterBottom>
          Stock Symbol Comparison
        </Typography>
        
        {/* Add Stock Controls */}
        <Stack direction="row" spacing={2} alignItems="center" sx={{ mb: 2 }}>
          <TextField
            size="small"
            label="Stock Symbol"
            value={newSymbol}
            onChange={(e) => setNewSymbol(e.target.value.toUpperCase())}
            onKeyPress={handleKeyPress}
            placeholder="e.g., VIC, VCB, FPT"
            sx={{ minWidth: 150 }}
          />
          <Button 
            variant="contained" 
            onClick={addStock}
            disabled={!newSymbol.trim()}
          >
            Add Stock
          </Button>
        </Stack>

        {/* Selected Stocks */}
        {stocksData.length > 0 && (
          <Box sx={{ mb: 2 }}>
            <Typography variant="subtitle2" gutterBottom>
              Selected Stocks:
            </Typography>
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
              {stocksData.map((stock) => (
                <Chip
                  key={stock.symbol}
                  label={stock.symbol}
                  onDelete={() => removeStock(stock.symbol)}
                  color={stock.loading ? "default" : stock.error ? "error" : "primary"}
                  variant={stock.loading ? "outlined" : "filled"}
                />
              ))}
            </Stack>
          </Box>
        )}

        {/* Period Controls */}
        <Stack direction="row" spacing={2} alignItems="center" sx={{ mb: 2 }} flexWrap>
          <ToggleButtonGroup
            value={period}
            exclusive
            onChange={(_, newPeriod) => newPeriod && setPeriod(newPeriod)}
            size="small"
          >
            {PERIODS.map(({ value, label }) => (
              <ToggleButton key={value} value={value}>
                {label}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>

          {/* Custom Date Picker */}
          {period === 'CUSTOM' && (
            <Box>
              <LocalizationProvider dateAdapter={AdapterDateFns}>
                <DatePicker
                  label="Start Date"
                  value={customStartDate}
                  onChange={(newValue) => setCustomStartDate(newValue)}
                  slotProps={{
                    textField: {
                      size: 'small',
                      sx: { minWidth: 150 }
                    }
                  }}
                />
              </LocalizationProvider>
              {dateRangeMessage && (
                <Typography
                  variant="body2"
                  sx={{ 
                    mt: 1, 
                    color: 'warning.main',
                    fontSize: '0.75rem'
                  }}
                >
                  {dateRangeMessage}
                </Typography>
              )}
            </Box>
          )}
        </Stack>

        {/* Chart */}
        <Box sx={{ height: 400, width: '100%' }}>
          {stocksData.length === 0 ? (
            <Box sx={{ 
              height: '100%', 
              display: 'flex', 
              alignItems: 'center', 
              justifyContent: 'center',
              border: '1px dashed #ccc',
              borderRadius: 1,
              color: 'text.secondary'
            }}>
              <Typography>Add stock symbols to compare their performance</Typography>
            </Box>
          ) : stocksData.some(stock => stock.loading) ? (
            <Box sx={{ 
              height: '100%', 
              display: 'flex', 
              alignItems: 'center', 
              justifyContent: 'center'
            }}>
              <CircularProgress />
            </Box>
          ) : !chartData || chartData.series.length === 0 ? (
            <Box sx={{ 
              height: '100%', 
              display: 'flex', 
              alignItems: 'center', 
              justifyContent: 'center',
              border: '1px dashed #ccc',
              borderRadius: 1,
              color: 'text.secondary'
            }}>
              <Typography>No valid data to display</Typography>
            </Box>
          ) : (
            <LineChart
              series={chartData.series}
              xAxis={[{
                data: chartData.timestamps.map(timestamp => new Date(timestamp)),
                scaleType: 'time',
                valueFormatter: (value: Date) => value.toLocaleDateString(),
              }]}
              yAxis={[{
                label: 'Returns (%)',
                valueFormatter: (value: number) => `${value.toFixed(1)}%`,
              }]}
              height={400}
              margin={{ left: 80, right: 20, top: 20, bottom: 60 }}
              grid={{ vertical: true, horizontal: true }}
            />
          )}
        </Box>

        {/* Loading/Error States */}
        {stocksData.some(stock => stock.error) && (
          <Box sx={{ mt: 2 }}>
            <Typography variant="subtitle2" color="error" gutterBottom>
              Errors:
            </Typography>
            {stocksData
              .filter(stock => stock.error)
              .map(stock => (
                <Typography key={stock.symbol} variant="body2" color="error">
                  {stock.symbol}: {stock.error}
                </Typography>
              ))}
          </Box>
        )}
      </CardContent>
    </Card>
  );
}
