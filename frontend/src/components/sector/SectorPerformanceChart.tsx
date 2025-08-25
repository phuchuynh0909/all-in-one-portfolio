import { useState, useEffect } from 'react';
import {
  Card,
  CardContent,
  Typography,
  Box,
  ToggleButton,
  ToggleButtonGroup,
  Chip,
  Stack,
  TextField,
  InputAdornment,
} from '@mui/material';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts';
import { Add as AddIcon, Remove as RemoveIcon } from '@mui/icons-material';


type TimePeriod = 'YTD' | '1M' | '3M' | '6M' | '1Y' | '3Y' | '5Y' | '10Y' | 'ALL';

const PERIODS: { value: TimePeriod; label: string }[] = [
  { value: 'YTD', label: 'YTD' },
  { value: '1M', label: '1M' },
  { value: '3M', label: '3M' },
  { value: '6M', label: '6M' },
  { value: '1Y', label: '1Y' },
  { value: '3Y', label: '3Y' },
  { value: '5Y', label: '5Y' },
  { value: '10Y', label: '10Y' },
  { value: 'ALL', label: 'All' },
];

const COLORS = [
  '#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd',
  '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf',
];

interface SectorTimeseries {
  timestamps: string[];
  sectors: {
    [key: string]: number[];
  };
}

export default function SectorPerformanceChart() {
  const [period, setPeriod] = useState<TimePeriod>('6M');
  const [topN, setTopN] = useState(10);
  const [selectedSectors, setSelectedSectors] = useState<string[]>([]);
  const [data, setData] = useState<SectorTimeseries | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const loadData = async () => {
      try {
        setLoading(true);
        setError(null);
        // Calculate start date based on period
        const endDate = new Date();
        let startDate = new Date();
        switch (period) {
          case '1M':
            startDate.setMonth(endDate.getMonth() - 1);
            break;
          case '3M':
            startDate.setMonth(endDate.getMonth() - 3);
            break;
          case '6M':
            startDate.setMonth(endDate.getMonth() - 6);
            break;
          case '1Y':
            startDate.setFullYear(endDate.getFullYear() - 1);
            break;
          case '3Y':
            startDate.setFullYear(endDate.getFullYear() - 3);
            break;
          case '5Y':
            startDate.setFullYear(endDate.getFullYear() - 5);
            break;
          case '10Y':
            startDate.setFullYear(endDate.getFullYear() - 10);
            break;
          case 'YTD':
            startDate = new Date(endDate.getFullYear(), 0, 1); // January 1st of current year
            break;
          case 'ALL':
            startDate = new Date(2000, 0, 1); // Default to year 2000
            break;
        }

        const result = await fetch('/api/v1/timeseries/sector/3', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            start_date: startDate.toISOString().split('T')[0],
            end_date: endDate.toISOString().split('T')[0],
            interval: "1d"
          })
        });
        const data = await result.json();
        setData(data);
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load sector data');
      } finally {
        setLoading(false);
      }
    };

    loadData();
  }, []);

  if (loading) return <Typography>Loading...</Typography>;
  if (error) return <Typography color="error">{error}</Typography>;
  if (!data) return null;

  // Transform data for Recharts and calculate percentage changes
  const chartData = data.timestamps.map((timestamp, index) => {
    const dataPoint: { [key: string]: any } = { date: timestamp };
    Object.entries(data.sectors).forEach(([sector, values]) => {
      // Calculate percentage change from initial value
      const initialValue = values[0];
      const currentValue = values[index];
      const percentageChange = ((currentValue - initialValue) / initialValue) * 100;
      dataPoint[sector] = Number(percentageChange.toFixed(2));
    });
    return dataPoint;
  });

  // Calculate latest returns for each sector
  const latestReturns = Object.entries(data.sectors).reduce((acc, [sector, values]) => {
    const initialValue = values[0];
    const currentValue = values[values.length - 1];
    acc[sector] = ((currentValue - initialValue) / initialValue) * 100;
    return acc;
  }, {} as Record<string, number>);

  return (
    <Card>
      <CardContent>
        <Typography variant="h6" gutterBottom>
          Sector Performance (Close)
        </Typography>

        <Stack direction="row" spacing={2} alignItems="center" sx={{ mb: 2 }}>
          <ToggleButtonGroup
            value={period}
            exclusive
            onChange={(_, value) => value && setPeriod(value)}
            size="small"
          >
            {PERIODS.map(({ value, label }) => (
              <ToggleButton key={value} value={value}>
                {label}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>

          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Typography variant="body2">Top performing sectors</Typography>
            <TextField
              size="small"
              type="number"
              value={topN}
              onChange={(e) => setTopN(Math.max(1, Math.min(20, Number(e.target.value))))}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <RemoveIcon
                      sx={{ cursor: 'pointer' }}
                      onClick={() => setTopN((n) => Math.max(1, n - 1))}
                    />
                  </InputAdornment>
                ),
                endAdornment: (
                  <InputAdornment position="end">
                    <AddIcon
                      sx={{ cursor: 'pointer' }}
                      onClick={() => setTopN((n) => Math.min(20, n + 1))}
                    />
                  </InputAdornment>
                ),
              }}
              sx={{ width: 100 }}
            />
          </Box>
        </Stack>

        <Box sx={{ mb: 2 }}>
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
            {Object.keys(data.sectors).map((sector) => (
              <Chip
                key={sector}
                label={`${sector} (${latestReturns[sector].toFixed(1)}%)`}
                onClick={() => {
                  if (selectedSectors.includes(sector)) {
                    setSelectedSectors(selectedSectors.filter((s) => s !== sector));
                  } else {
                    setSelectedSectors([...selectedSectors, sector]);
                  }
                }}
                color={selectedSectors.includes(sector) ? 'primary' : 'default'}
                sx={{ mb: 1 }}
              />
            ))}
          </Stack>
        </Box>

        <Box sx={{ width: '100%', height: 400 }}>
          <ResponsiveContainer>
            <LineChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis
                dataKey="date"
                tickFormatter={(value) => new Date(value).toLocaleDateString()}
              />
              <YAxis
                tickFormatter={(value) => `${value.toFixed(1)}%`}
                domain={['dataMin', 'dataMax']}
              />
              <Tooltip
                formatter={(value: number) => `${value.toFixed(2)}%`}
                labelFormatter={(label) => new Date(label as string).toLocaleDateString()}
              />
              <Legend />
              {Object.keys(data.sectors)
                .filter(sector => selectedSectors.length === 0 || selectedSectors.includes(sector))
                .map((sector, i) => (
                <Line
                  key={sector}
                  type="monotone"
                  dataKey={sector}
                  stroke={COLORS[i % COLORS.length]}
                  dot={false}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </Box>
      </CardContent>
    </Card>
  );
}
