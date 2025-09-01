import { useState, useEffect } from 'react';
import { 
  Card, 
  CardContent, 
  Typography, 
  Box, 
  FormControl, 
  InputLabel, 
  Select, 
  MenuItem, 
  TextField, 
  Button,
  Autocomplete,
  Chip
} from '@mui/material';
import { PieChart } from '@mui/x-charts/PieChart';
import { optimizePortfolio, getAllStockSymbols } from '../../lib/services/portfolio';
import type { OptimizationMethod, OptimizationResult, StockSymbol } from '../../lib/services/portfolio';

interface PortfolioPieChartProps {
  tickers: string[];
}

export default function PortfolioPieChart({ tickers }: PortfolioPieChartProps) {
  const [method, setMethod] = useState<OptimizationMethod>('ef');
  const [riskFreeRate, setRiskFreeRate] = useState<number>(0.0);
  const [result, setResult] = useState<OptimizationResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  
  // Symbol selection state
  const [availableSymbols, setAvailableSymbols] = useState<StockSymbol[]>([]);
  const [selectedSymbols, setSelectedSymbols] = useState<string[]>([...new Set(tickers.map(ticker => ticker.toUpperCase()))]);
  const [symbolsLoading, setSymbolsLoading] = useState(false);

  // Load available symbols on component mount
  useEffect(() => {
    const loadSymbols = async () => {
      try {
        setSymbolsLoading(true);
        const symbols = await getAllStockSymbols(500); // Limit to 500 for performance
        setAvailableSymbols(symbols);
      } catch (e) {
        console.error('Error loading symbols:', e);
      } finally {
        setSymbolsLoading(false);
      }
    };
    
    loadSymbols();
  }, []);

  // Update selected symbols when tickers prop changes
  useEffect(() => {
    // Remove duplicates from initial tickers as well
    const uniqueTickers = [...new Set(tickers.map(ticker => ticker.toUpperCase()))];
    setSelectedSymbols(uniqueTickers);
  }, [tickers]);

  const handleOptimize = async () => {
    try {
      setLoading(true);
      setError(null);
      const optimizationResult = await optimizePortfolio({
        tickers: selectedSymbols,
        method,
        risk_free_rate: riskFreeRate,
      });
      setResult(optimizationResult);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to optimize portfolio');
    } finally {
      setLoading(false);
    }
  };

  const pieData = result
    ? Object.entries(result.weights).map(([ticker, weight]) => ({
        id: ticker,
        value: weight,
        label: `${ticker} (${(weight * 100).toFixed(1)}%)`,
      }))
    : [];

  return (
    <Card>
      <CardContent>
        <Typography variant="h6" gutterBottom>
          Portfolio Optimization Weights
        </Typography>

        <Box sx={{ mb: 2 }}>
          <FormControl fullWidth sx={{ mb: 2 }}>
            <InputLabel>Optimization Method</InputLabel>
            <Select
              value={method}
              label="Optimization Method"
              onChange={(e) => setMethod(e.target.value as OptimizationMethod)}
            >
              <MenuItem value="ef">Efficient Frontier (Max Sharpe)</MenuItem>
              <MenuItem value="hrp">Hierarchical Risk Parity</MenuItem>
              <MenuItem value="cvar">Conditional Value at Risk</MenuItem>
              <MenuItem value="cla">Critical Line Algorithm</MenuItem>
            </Select>
          </FormControl>

          <TextField
            fullWidth
            type="number"
            label="Risk-Free Rate (%)"
            value={riskFreeRate * 100}
            onChange={(e) => setRiskFreeRate(Number(e.target.value) / 100)}
            sx={{ mb: 2 }}
          />

          <Autocomplete
            multiple
            freeSolo
            fullWidth
            options={availableSymbols.map(symbol => symbol.symbol)}
            value={selectedSymbols}
            onChange={(_, newValue) => {
              // Handle both string values (custom input) and selected values
              const symbols = newValue.map(value => 
                typeof value === 'string' ? value.toUpperCase() : value
              );
              // Remove duplicates
              const uniqueSymbols = [...new Set(symbols)];
              setSelectedSymbols(uniqueSymbols);
            }}
            loading={symbolsLoading}
            renderTags={(tagValue, getTagProps) =>
              tagValue.map((option, index) => (
                <Chip
                  label={option}
                  {...getTagProps({ index })}
                  key={option}
                  size="small"
                  color={availableSymbols.some(s => s.symbol === option) ? "primary" : "default"}
                />
              ))
            }
            renderInput={(params) => (
              <TextField
                {...params}
                label="Select or Type Symbols for Optimization"
                placeholder={selectedSymbols.length === 0 ? "Choose from list or type custom symbols..." : ""}
                helperText={`${selectedSymbols.length} symbols selected (blue = verified, gray = custom)`}
              />
            )}
            sx={{ mb: 2 }}
            filterSelectedOptions
          />

          <Button
            variant="contained"
            onClick={handleOptimize}
            disabled={loading || selectedSymbols.length === 0}
            fullWidth
          >
            {loading ? 'Optimizing...' : 'Optimize Portfolio'}
          </Button>
        </Box>

        {error && (
          <Typography color="error" sx={{ mb: 2 }}>
            {error}
          </Typography>
        )}

        {result && (
          <Box>
            <Typography variant="subtitle2" gutterBottom>
              Performance Metrics:
            </Typography>
            <Typography variant="body2">
              Expected Return: {result.expected_return !== null ? `${(result.expected_return * 100).toFixed(2)}%` : 'N/A'}
            </Typography>
            <Typography variant="body2">
              Volatility: {result.volatility !== null ? `${(result.volatility * 100).toFixed(2)}%` : 'N/A'}
            </Typography>
            <Typography variant="body2" gutterBottom>
              Sharpe Ratio: {result.sharpe_ratio !== null ? result.sharpe_ratio.toFixed(2) : 'N/A'}
            </Typography>

            <Box sx={{ width: '100%', height: 300 }}>
              <PieChart
                series={[
                  {
                    data: pieData,
                    highlightScope: { fade: 'global', highlight: 'item' },
                    arcLabel: 'label',
                  },
                ]}
                height={300}
                margin={{ right: 5 }}
                slotProps={{
                  legend: {
                    position: { vertical: 'bottom', horizontal: 'center' }
                  },
                }}
              />
            </Box>
          </Box>
        )}
      </CardContent>
    </Card>
  );
}