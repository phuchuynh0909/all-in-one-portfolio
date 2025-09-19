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
  Chip,
  Alert,
  Grid,
  IconButton
} from '@mui/material';
import { Add as AddIcon, Delete as DeleteIcon } from '@mui/icons-material';
import { PieChart } from '@mui/x-charts/PieChart';
import { optimizePortfolio, getAllStockSymbols } from '../../lib/services/portfolio';
import type { OptimizationMethod, RiskModel, OptimizationResult, StockSymbol } from '../../lib/services/portfolio';

interface PortfolioPieChartProps {
  tickers: string[];
}

// Risk model descriptions and use cases
const getRiskModelInfo = (riskModel: RiskModel) => {
  const riskModelInfo: Record<RiskModel, {
    name: string;
    description: string;
    strengths: string;
    weaknesses: string;
    useCase: string;
  }> = {
    'sample_cov': {
      name: 'Sample Covariance',
      description: 'The plain sample covariance matrix computed directly from historical returns.',
      strengths: 'Simple, intuitive, unbiased in large samples.',
      weaknesses: 'Highly unstable in small samples or when the number of assets is large compared to the number of observations.',
      useCase: 'Baseline model for traditional mean-variance portfolio optimization.'
    },
    'semicovariance': {
      name: 'Semicovariance',
      description: 'A covariance-like measure that only considers downside deviations (returns below a target or mean).',
      strengths: 'Focuses on downside risk, which better reflects investor concerns.',
      weaknesses: 'Ignores upside variation, so may understate total volatility.',
      useCase: 'Downside-risk optimization (e.g., Sortino ratio, downside VaR), investor preference-sensitive risk modeling.'
    },
    'exp_cov': {
      name: 'Exponentially Weighted Covariance',
      description: 'Covariance estimated with exponentially decaying weights (recent data more important).',
      strengths: 'Captures time-varying volatility, adjusts faster to regime shifts and crises.',
      weaknesses: 'Requires choosing a decay parameter (λ); too high = slow response, too low = noisy.',
      useCase: 'Market risk systems (e.g., RiskMetrics), adaptive risk management during volatile periods.'
    },
    'ledoit_wolf': {
      name: 'Ledoit-Wolf Shrinkage',
      description: 'A shrinkage estimator combining the sample covariance with a structured target using an optimal shrinkage intensity determined analytically.',
      strengths: 'Reduces estimation error, well-suited for high-dimensional problems.',
      weaknesses: 'Assumes shrinkage target is appropriate; may not capture complex structures.',
      useCase: 'Portfolio optimization when assets >> observations (e.g., large equity universes).'
    },
    'ledoit_wolf_constant_variance': {
      name: 'Ledoit-Wolf (Constant Variance)',
      description: 'Ledoit-Wolf shrinkage where the target is a constant variance diagonal matrix (all variances equal, no correlations).',
      strengths: 'Very stable, guards against noisy variance/correlation estimates.',
      weaknesses: 'May oversimplify risk structure by assuming equal variances.',
      useCase: 'Risk models where only relative exposures matter, or when you want to avoid spurious correlations.'
    },
    'ledoit_wolf_single_factor': {
      name: 'Ledoit-Wolf (Single Factor)',
      description: 'Ledoit-Wolf shrinkage toward a single-factor model (e.g., market factor).',
      strengths: 'Uses a parsimonious structure that captures systematic risk while stabilizing covariance.',
      weaknesses: 'Limited to single-factor structure, may miss multi-factor relationships.',
      useCase: 'Equity portfolios with strong common factor structure (CAPM-style risk modeling).'
    },
    'ledoit_wolf_constant_correlation': {
      name: 'Ledoit-Wolf (Constant Correlation)',
      description: 'Ledoit-Wolf shrinkage toward a constant correlation matrix (all pairwise correlations are the same).',
      strengths: 'Stabilizes correlation estimates, avoids overfitting spurious relationships.',
      weaknesses: 'Assumes all assets have the same correlation, which may be unrealistic.',
      useCase: 'Diversification analysis, portfolio construction when sample correlations are unreliable.'
    },
    'oracle_approximating': {
      name: 'Oracle Approximating Shrinkage (OAS)',
      description: 'An improvement over Ledoit-Wolf that more closely approximates the "oracle" shrinkage intensity.',
      strengths: 'Provides lower mean squared error than standard Ledoit-Wolf, especially in high-dimensional data.',
      weaknesses: 'More complex computation, may be sensitive to data quality.',
      useCase: 'Preferred shrinkage estimator for portfolio optimization when many assets and limited history.'
    }
  };
  
  // Safety check - return default if riskModel is invalid
  return riskModelInfo[riskModel] || riskModelInfo['sample_cov'];
};

export default function PortfolioPieChart({ tickers }: PortfolioPieChartProps) {
  const [method, setMethod] = useState<OptimizationMethod>('ef');
  const [riskModel, setRiskModel] = useState<RiskModel>('sample_cov');
  const [riskFreeRate, setRiskFreeRate] = useState<number>(0.0);
  const [result, setResult] = useState<OptimizationResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  
  // Additional parameters for specific optimization methods
  const [riskAversion, setRiskAversion] = useState<number>(1.0);
  const [targetRisk, setTargetRisk] = useState<number>(0.15);
  const [targetReturn, setTargetReturn] = useState<number>(0.12);
  
  // Black-Litterman specific parameters
  const [marketCaps, setMarketCaps] = useState<Record<string, number>>({});
  const [views, setViews] = useState<Record<string, number>>({});
  const [viewConfidences, setViewConfidences] = useState<Record<string, number>>({});

  // Compute risk model info once per render
  const currentRiskModelInfo = getRiskModelInfo(riskModel);
  
  // Debug: Log when risk model changes
  console.log('Current risk model:', riskModel, 'Info:', currentRiskModelInfo.name);
  
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
      
      // Build optimization request with conditional parameters
      const request: any = {
        tickers: selectedSymbols,
        method,
        risk_model: riskModel,
        risk_free_rate: riskFreeRate,
      };
      
      // Add method-specific parameters
      if (method === 'max_quadratic_utility') {
        request.risk_aversion = riskAversion;
      } else if (method === 'efficient_risk') {
        request.target_risk = targetRisk;
      } else if (method === 'efficient_return') {
        request.target_return = targetReturn;
      } else if (method === 'black_litterman') {
        request.risk_aversion = riskAversion;
        if (Object.keys(marketCaps).length > 0) {
          request.market_caps = marketCaps;
        }
        if (Object.keys(views).length > 0) {
          request.views = views;
        }
        if (Object.keys(viewConfidences).length > 0) {
          request.view_confidences = viewConfidences;
        }
      }
      
      const optimizationResult = await optimizePortfolio(request);
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
              <MenuItem value="ef">Efficient Frontier (Legacy)</MenuItem>
              <MenuItem value="max_sharpe">Maximum Sharpe Ratio</MenuItem>
              <MenuItem value="min_volatility">Minimum Volatility</MenuItem>
              <MenuItem value="max_quadratic_utility">Maximum Quadratic Utility</MenuItem>
              <MenuItem value="efficient_risk">Efficient Risk (Target Risk)</MenuItem>
              <MenuItem value="efficient_return">Efficient Return (Target Return)</MenuItem>
              <MenuItem value="black_litterman">Black-Litterman</MenuItem>
              <MenuItem value="hrp">Hierarchical Risk Parity</MenuItem>
              <MenuItem value="cvar">Conditional Value at Risk</MenuItem>
              <MenuItem value="cla">Critical Line Algorithm</MenuItem>
            </Select>
          </FormControl>

          <FormControl fullWidth sx={{ mb: 2 }}>
            <InputLabel>Risk Model</InputLabel>
            <Select
              value={riskModel}
              label="Risk Model"
              onChange={(e) => setRiskModel(e.target.value as RiskModel)}
            >
              {(['sample_cov', 'semicovariance', 'exp_cov', 'ledoit_wolf', 'ledoit_wolf_constant_variance', 'ledoit_wolf_single_factor', 'ledoit_wolf_constant_correlation', 'oracle_approximating'] as RiskModel[]).map((model) => {
                const info = getRiskModelInfo(model);
                return (
                  <MenuItem key={model} value={model}>
                    {info.name}
                  </MenuItem>
                );
              })}
            </Select>
          </FormControl>

          {/* Risk Model Information Box */}
          <Alert severity="info" sx={{ mb: 2 }} key={riskModel}>
            <Typography variant="subtitle2" gutterBottom>
              {currentRiskModelInfo.name}
            </Typography>
            <Typography variant="body2" paragraph>
              <strong>Description:</strong> {currentRiskModelInfo.description}
            </Typography>
            <Typography variant="body2" paragraph>
              <strong>Strengths:</strong> {currentRiskModelInfo.strengths}
            </Typography>
            <Typography variant="body2" paragraph>
              <strong>Weaknesses:</strong> {currentRiskModelInfo.weaknesses}
            </Typography>
            <Typography variant="body2">
              <strong>Best Use Case:</strong> {currentRiskModelInfo.useCase}
            </Typography>
          </Alert>

          <TextField
            fullWidth
            type="number"
            label="Risk-Free Rate (%)"
            value={riskFreeRate * 100}
            onChange={(e) => setRiskFreeRate(Number(e.target.value) / 100)}
            sx={{ mb: 2 }}
          />

          {/* Conditional parameter inputs based on optimization method */}
          {(method === 'max_quadratic_utility' || method === 'black_litterman') && (
            <TextField
              fullWidth
              type="number"
              label="Risk Aversion"
              value={riskAversion}
              onChange={(e) => setRiskAversion(Number(e.target.value))}
              helperText="Higher values indicate more risk-averse behavior (typical range: 1-10)"
              sx={{ mb: 2 }}
            />
          )}

          {method === 'efficient_risk' && (
            <TextField
              fullWidth
              type="number"
              label="Target Risk (Volatility)"
              value={targetRisk}
              onChange={(e) => setTargetRisk(Number(e.target.value))}
              helperText="Enter target volatility as a decimal (e.g., 0.15 for 15%)"
              sx={{ mb: 2 }}
            />
          )}

          {method === 'efficient_return' && (
            <TextField
              fullWidth
              type="number"
              label="Target Return"
              value={targetReturn}
              onChange={(e) => setTargetReturn(Number(e.target.value))}
              helperText="Enter target return as a decimal (e.g., 0.12 for 12%)"
              sx={{ mb: 2 }}
            />
          )}

          {method === 'black_litterman' && (
            <Box sx={{ mb: 2 }}>
              <Alert severity="info" sx={{ mb: 2 }}>
                <Typography variant="subtitle2" gutterBottom>
                  Black-Litterman Model
                </Typography>
                <Typography variant="body2">
                  <strong>Market Caps:</strong> Enter market capitalizations for equilibrium portfolio (optional - will use equal weights if not provided).<br/>
                  <strong>Views:</strong> Enter your expected returns for specific assets (optional).<br/>
                  <strong>Confidences:</strong> Enter confidence levels for your views - lower values = more confident (optional).
                </Typography>
              </Alert>
              
              <Typography variant="subtitle2" gutterBottom>
                Market Capitalizations (Optional)
              </Typography>
              <Box sx={{ mb: 2 }}>
                {Object.entries(marketCaps).map(([ticker, value]) => (
                  <Grid container spacing={2} key={`market-cap-${ticker}`} sx={{ mb: 1 }}>
                    <Grid item xs={4}>
                      <TextField
                        fullWidth
                        size="small"
                        label="Ticker"
                        value={ticker}
                        onChange={(e) => {
                          const newMarketCaps = { ...marketCaps };
                          delete newMarketCaps[ticker];
                          newMarketCaps[e.target.value] = value;
                          setMarketCaps(newMarketCaps);
                        }}
                      />
                    </Grid>
                    <Grid item xs={6}>
                      <TextField
                        fullWidth
                        size="small"
                        type="number"
                        label="Market Cap"
                        value={value}
                        onChange={(e) => {
                          setMarketCaps(prev => ({
                            ...prev,
                            [ticker]: Number(e.target.value)
                          }));
                        }}
                      />
                    </Grid>
                    <Grid item xs={2}>
                      <IconButton
                        size="small"
                        onClick={() => {
                          const newMarketCaps = { ...marketCaps };
                          delete newMarketCaps[ticker];
                          setMarketCaps(newMarketCaps);
                        }}
                      >
                        <DeleteIcon />
                      </IconButton>
                    </Grid>
                  </Grid>
                ))}
                <Button
                  startIcon={<AddIcon />}
                  onClick={() => {
                    setMarketCaps(prev => ({ ...prev, '': 0 }));
                  }}
                  size="small"
                >
                  Add Market Cap
                </Button>
              </Box>

              <Typography variant="subtitle2" gutterBottom>
                Investment Views (Optional)
              </Typography>
              <Box sx={{ mb: 2 }}>
                {Object.entries(views).map(([ticker, value]) => (
                  <Grid container spacing={2} key={`view-${ticker}`} sx={{ mb: 1 }}>
                    <Grid item xs={4}>
                      <TextField
                        fullWidth
                        size="small"
                        label="Ticker"
                        value={ticker}
                        onChange={(e) => {
                          const newViews = { ...views };
                          delete newViews[ticker];
                          newViews[e.target.value] = value;
                          setViews(newViews);
                        }}
                      />
                    </Grid>
                    <Grid item xs={6}>
                      <TextField
                        fullWidth
                        size="small"
                        type="number"
                        label="Expected Return (decimal)"
                        value={value}
                        onChange={(e) => {
                          setViews(prev => ({
                            ...prev,
                            [ticker]: Number(e.target.value)
                          }));
                        }}
                        helperText="e.g., 0.15 for 15%"
                      />
                    </Grid>
                    <Grid item xs={2}>
                      <IconButton
                        size="small"
                        onClick={() => {
                          const newViews = { ...views };
                          delete newViews[ticker];
                          setViews(newViews);
                        }}
                      >
                        <DeleteIcon />
                      </IconButton>
                    </Grid>
                  </Grid>
                ))}
                <Button
                  startIcon={<AddIcon />}
                  onClick={() => {
                    setViews(prev => ({ ...prev, '': 0 }));
                  }}
                  size="small"
                >
                  Add Investment View
                </Button>
              </Box>

              <Typography variant="subtitle2" gutterBottom>
                View Confidences (Optional)
              </Typography>
              <Box sx={{ mb: 2 }}>
                {Object.entries(viewConfidences).map(([ticker, value]) => (
                  <Grid container spacing={2} key={`confidence-${ticker}`} sx={{ mb: 1 }}>
                    <Grid item xs={4}>
                      <TextField
                        fullWidth
                        size="small"
                        label="Ticker"
                        value={ticker}
                        onChange={(e) => {
                          const newConfidences = { ...viewConfidences };
                          delete newConfidences[ticker];
                          newConfidences[e.target.value] = value;
                          setViewConfidences(newConfidences);
                        }}
                      />
                    </Grid>
                    <Grid item xs={6}>
                      <TextField
                        fullWidth
                        size="small"
                        type="number"
                        label="Confidence Level"
                        value={value}
                        onChange={(e) => {
                          setViewConfidences(prev => ({
                            ...prev,
                            [ticker]: Number(e.target.value)
                          }));
                        }}
                        helperText="Lower = more confident (0.05-0.5)"
                      />
                    </Grid>
                    <Grid item xs={2}>
                      <IconButton
                        size="small"
                        onClick={() => {
                          const newConfidences = { ...viewConfidences };
                          delete newConfidences[ticker];
                          setViewConfidences(newConfidences);
                        }}
                      >
                        <DeleteIcon />
                      </IconButton>
                    </Grid>
                  </Grid>
                ))}
                <Button
                  startIcon={<AddIcon />}
                  onClick={() => {
                    setViewConfidences(prev => ({ ...prev, '': 0.1 }));
                  }}
                  size="small"
                >
                  Add View Confidence
                </Button>
              </Box>
            </Box>
          )}

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