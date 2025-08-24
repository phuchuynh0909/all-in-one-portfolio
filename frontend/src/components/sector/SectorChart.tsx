import { useEffect, useState } from 'react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts';
import { Box, Typography, CircularProgress } from '@mui/material';
import { apiGet } from '../../lib/api';

type Sector = {
  id: number;
  level: number;
  name: string;
  vonhoa_d: number;
  pe_d: number;
  pb_d: number;
  roe_ttm: number;
};

type ChartData = {
  name: string;
  marketCap: number;
  pe: number;
  pb: number;
  roe: number;
};

export default function SectorChart({
  level = 3,
  limit = 10,
}: {
  level?: number;
  limit?: number;
}) {
  const [data, setData] = useState<ChartData[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    apiGet<Sector[]>(`/sector/list/${level}`)
      .then((sectors) => {
        // Transform and sort by market cap
        const chartData = sectors
          .filter(
            (s) =>
              s.vonhoa_d != null &&
              s.pe_d != null &&
              s.pb_d != null &&
              s.roe_ttm != null
          )
          .map((s) => ({
            name: s.name || `Sector ${s.id}`,
            marketCap: Math.round(s.vonhoa_d),
            pe: Number(s.pe_d),
            pb: Number(s.pb_d),
            roe: Number(s.roe_ttm),
          }))
          .sort((a, b) => b.marketCap - a.marketCap)
          .slice(0, limit);

        setData(chartData);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [level, limit]);

  if (error) return <Typography color="error">{error}</Typography>;
  if (loading) return <CircularProgress />;

  return (
    <Box sx={{ width: '100%', height: 400 }}>
      <Typography variant="h6" gutterBottom>
        Top {limit} Sectors by Market Cap
      </Typography>
      <ResponsiveContainer>
        <BarChart
          data={data}
          margin={{ top: 20, right: 30, left: 20, bottom: 5 }}
        >
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis
            dataKey="name"
            angle={-45}
            textAnchor="end"
            height={100}
            interval={0}
          />
          <YAxis yAxisId="left" />
          <YAxis yAxisId="right" orientation="right" />
          <Tooltip />
          <Legend />
          <Bar
            yAxisId="left"
            dataKey="marketCap"
            name="Market Cap (B)"
            fill="#8884d8"
          />
          <Bar
            yAxisId="right"
            dataKey="roe"
            name="ROE (%)"
            fill="#82ca9d"
          />
        </BarChart>
      </ResponsiveContainer>
    </Box>
  );
}
