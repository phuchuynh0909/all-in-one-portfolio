import { useState, useEffect } from 'react';
import {
  Box,
  Typography,
  CircularProgress,
  Card,
  CardContent,
  ToggleButton,
  ToggleButtonGroup,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Chip,
  Stack,
  OutlinedInput,
} from '@mui/material';
import { LineChart } from '@mui/x-charts/LineChart';

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

import { type SectorTimeseries, fetchSectorTimeseries } from '../../lib/services/timeseries';

// Helper function to calculate date range based on period
const getStartDateForPeriod = (period: TimePeriod): string => {
  const now = new Date();
  let startDate: Date;

  switch (period) {
    case 'YTD':
      startDate = new Date(now.getFullYear(), 0, 1); // January 1st of current year
      break;
    case '1M':
      startDate = new Date(now.getFullYear(), now.getMonth() - 1, now.getDate());
      break;
    case '3M':
      startDate = new Date(now.getFullYear(), now.getMonth() - 3, now.getDate());
      break;
    case '6M':
      startDate = new Date(now.getFullYear(), now.getMonth() - 6, now.getDate());
      break;
    case '1Y':
      startDate = new Date(now.getFullYear() - 1, now.getMonth(), now.getDate());
      break;
    case '3Y':
      startDate = new Date(now.getFullYear() - 3, now.getMonth(), now.getDate());
      break;
    case '5Y':
      startDate = new Date(now.getFullYear() - 5, now.getMonth(), now.getDate());
      break;
    case '10Y':
      startDate = new Date(now.getFullYear() - 10, now.getMonth(), now.getDate());
      break;
    default: // 'ALL'
      startDate = new Date('2020-01-01'); // Start from a reasonable past date
  }

  return startDate.toISOString().split('T')[0];
};

export default function SectorChart({
  level = 3,
}: {
  level: number;
}) {
  const [data, setData] = useState<SectorTimeseries | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [period, setPeriod] = useState<TimePeriod>('6M');
  const [topN, setTopN] = useState<number>(10);
  const [selectedSectors, setSelectedSectors] = useState<string[]>([]);

  useEffect(() => {
    const loadData = async () => {
      try {
        setLoading(true);
        setError(null);
        
        // Always fetch full data, no date filtering on API
        const result = await fetchSectorTimeseries(level, {
          start_date: "2020-01-01", // Get all available data
          end_date: new Date().toISOString().split('T')[0],
          interval: "1d"
        });
        setData(result);
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load sector data');
      } finally {
        setLoading(false);
      }
    };

    loadData();
  }, [level]); // Only reload when level changes, not period

  // Filter data based on selected period (frontend filtering)
  const filterDataByPeriod = (timestamps: string[], sectorData: any[], period: TimePeriod) => {
    if (period === 'ALL') {
      return { timestamps, sectorData };
    }

    const startDate = getStartDateForPeriod(period);
    const startTime = new Date(startDate).getTime();
    
    // Find the index where data should start
    const startIndex = timestamps.findIndex(timestamp => {
      return new Date(timestamp).getTime() >= startTime;
    });

    if (startIndex === -1) {
      // If no data found for the period, return empty
      return { timestamps: [], sectorData: [] };
    }

    // Filter timestamps
    const filteredTimestamps = timestamps.slice(startIndex);
    
    // Filter sector data
    const filteredSectorData = sectorData.map(sector => ({
      ...sector,
      data: sector.data.slice(startIndex)
    }));

    return { 
      timestamps: filteredTimestamps, 
      sectorData: filteredSectorData 
    };
  };

  // Calculate normalized performance data and set default selections
  useEffect(() => {
    if (!data || !data.sector_data) {
      setSelectedSectors([]);
      return;
    }

    // Apply period filtering first
    const { sectorData: filteredSectorData } = 
      filterDataByPeriod(data.timestamps, data.sector_data, period);

    // Calculate final performance for each sector (like Streamlit)
    const sectorPerformance: Array<{ sector: string; performance: number }> = [];
    
    filteredSectorData.forEach((sectorData) => {
      if (sectorData.data && sectorData.data.length > 0) {
        const initialValue = sectorData.data[0];
        const finalValue = sectorData.data[sectorData.data.length - 1];
        if (initialValue !== 0 && initialValue !== undefined && finalValue !== undefined) {
          const performance = ((finalValue - initialValue) / initialValue) * 100;
          sectorPerformance.push({ sector: sectorData.name, performance });
        }
      }
    });

    // Sort by performance (descending) and take top N
    sectorPerformance.sort((a, b) => b.performance - a.performance);
    const topSectors = sectorPerformance.slice(0, topN).map(item => item.sector);
    setSelectedSectors(topSectors);
  }, [data, topN, period]); // Added period dependency

  if (loading) return <CircularProgress />;
  if (error) return <Typography color="error">{error}</Typography>;
  if (!data || !data.sector_data) return null;

  // Apply period filtering
  const { timestamps: filteredTimestamps, sectorData: filteredSectorData } = 
    filterDataByPeriod(data.timestamps, data.sector_data, period);

  // Transform data for MUI X Charts - calculate normalized returns (like Streamlit)
  const chartSeries = selectedSectors.map((sector, index) => {
    const sectorData = filteredSectorData.find(s => s.name === sector);
    if (!sectorData || !sectorData.data || sectorData.data.length === 0) {
      return {
        data: [],
        label: sector,
        color: COLORS[index % COLORS.length],
        // curve: 'linear' as const,
        showMark: false,
      };
    }

    const initialValue = sectorData.data[0];
    const normalizedData = sectorData.data.map((value: number) => {
      if (initialValue !== 0 && initialValue !== undefined && value !== undefined) {
        return Number(((value / initialValue - 1) * 100).toFixed(4));
      }
      return 0;
    });

    return {
      data: normalizedData,
      label: sector,
      color: COLORS[index % COLORS.length],
      // curve: 'linear' as const,
      showMark: false,
    };
  });

  // Get all available sectors
  const allSectors = filteredSectorData.map(sectorData => sectorData.name);
  
  // Calculate latest performance for chip display (based on filtered period)
  const latestPerformance: Record<string, number> = {};
  filteredSectorData.forEach((sectorData) => {
    if (sectorData.data && sectorData.data.length > 0) {
      const initialValue = sectorData.data[0];
      const finalValue = sectorData.data[sectorData.data.length - 1];
      if (initialValue !== 0 && initialValue !== undefined && finalValue !== undefined) {
        latestPerformance[sectorData.name] = ((finalValue - initialValue) / initialValue) * 100;
      }
    }
  });

  // Sort sectors by performance for consistent ordering
  const sortedSectors = allSectors.sort((a, b) => (latestPerformance[b] || 0) - (latestPerformance[a] || 0));

  return (
    <Card>
      <CardContent>
        <Typography variant="h6" gutterBottom>
          Sector Performance (Close) - Level {level}
        </Typography>

        {/* Controls */}
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

          <FormControl size="small" sx={{ minWidth: 120 }}>
            <InputLabel>Top N</InputLabel>
            <Select
              value={topN}
              label="Top N"
              onChange={(e) => setTopN(e.target.value as number)}
            >
              {[5, 10, 15, 20, 25].map(num => (
                <MenuItem key={num} value={num}>{num}</MenuItem>
              ))}
            </Select>
          </FormControl>
        </Stack>

        {/* Sector selection dropdown */}
        <Box sx={{ mb: 2 }}>
          <FormControl sx={{ width: '100%' }}>
            <InputLabel>Select sectors to display</InputLabel>
            <Select
              multiple
              value={selectedSectors}
              onChange={(e) => setSelectedSectors(e.target.value as string[])}
              input={<OutlinedInput label="Select sectors to display" />}
              renderValue={(selected) => (
                <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                  {selected.map((value) => (
                    <Chip
                      key={value}
                      label={`${value} (${(latestPerformance[value] || 0).toFixed(1)}%)`}
                      size="small"
                      color="primary"
                    />
                  ))}
                </Box>
              )}
            >
              {sortedSectors.map((sector) => (
                <MenuItem key={sector} value={sector}>
                  {sector} ({(latestPerformance[sector] || 0).toFixed(2)}%)
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        </Box>

        {/* Chart */}
        <Box sx={{ width: '100%', height: 500 }}>
          {chartSeries.length > 0 ? (
            <LineChart
              xAxis={[{
                data: filteredTimestamps.map((_, index) => index),
                valueFormatter: (value: number) => {
                  const timestamp = filteredTimestamps[value];
                  return new Date(timestamp).toLocaleDateString();
                },
                scaleType: 'point' as const,
              }]}
              yAxis={[{
                valueFormatter: (value: number) => `${value.toFixed(1)} %`,
              }]}
              series={chartSeries}
              width={undefined}
              height={500}
              // grid={{ horizontal: true, vertical: true }}
              margin={{ left: 80, right: 80, top: 80, bottom: 100 }}
            />
          ) : (
            <Box sx={{ 
              display: 'flex', 
              alignItems: 'center', 
              justifyContent: 'center', 
              height: 500,
              color: 'text.secondary'
            }}>
              <Typography>No sectors selected</Typography>
            </Box>
          )}
        </Box>

        {selectedSectors.length === 0 && (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
            Please select at least one sector to display the chart.
          </Typography>
        )}
      </CardContent>
    </Card>
  );
}