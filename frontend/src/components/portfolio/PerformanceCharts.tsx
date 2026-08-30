import { Box, Typography, Grid } from '@mui/material';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Cell,
  Tooltip,
  ResponsiveContainer,
  Label,
  LabelList,
} from 'recharts';
import { useChartTheme } from '../../theme';

type Position = {
  id: number;
  ticker: string;
  quantity: number;
  purchase_price: number;
  current_price: number;
  purchase_date: string;
  notes?: string;
  created_at: string;
};

type AggregatedPosition = {
  ticker: string;
  total_quantity: number;
  total_cost_basis: number;
  total_current_value: number;
  total_unrealized_gain: number;
  return_pct: number;
};

type PerformanceChartsProps = {
  positions: Position[];
};

export default function PerformanceCharts({ positions }: PerformanceChartsProps) {
  const ct = useChartTheme();
  // Aggregate positions by ticker
  const aggregatedPositions = positions.reduce<Record<string, AggregatedPosition>>((acc, pos) => {
    if (!acc[pos.ticker]) {
      acc[pos.ticker] = {
        ticker: pos.ticker,
        total_quantity: 0,
        total_cost_basis: 0,
        total_current_value: 0,
        total_unrealized_gain: 0,
        return_pct: 0,
      };
    }
    
    acc[pos.ticker].total_quantity += Math.round(pos.quantity);
    const cost_basis = pos.purchase_price * pos.quantity;
    const current_value = pos.current_price * pos.quantity;
    acc[pos.ticker].total_cost_basis += Math.round(cost_basis);
    acc[pos.ticker].total_current_value += Math.round(current_value);
    acc[pos.ticker].total_unrealized_gain += Math.round(current_value - cost_basis);
    
    // Calculate weighted average return
    acc[pos.ticker].return_pct = 
      (acc[pos.ticker].total_current_value / acc[pos.ticker].total_cost_basis - 1) * 100;
    
    return acc;
  }, {});

  // Convert to array and sort by return percentage
  const sortedPositions = Object.values(aggregatedPositions)
    .sort((a, b) => b.return_pct - a.return_pct);
  
  // Get top 5 and bottom 5 performers
  const topPerformers = sortedPositions.slice(0, 5);
  const bottomPerformers = sortedPositions.slice(-5).reverse();

  const formatYAxis = (value: number) => `${Math.round(value)}%`;
  const formatTooltip = (value: number) => {
    return `${Math.round(value)}%`;
  };

  const chartHeight = 400;

  return (
    <Box sx={{ mt: 4, mb: 4 }}>
      <Grid container spacing={2}>
        <Grid item xs={12} md={6}>
          <Typography variant="h6" gutterBottom align="center" color="text.primary">
            Top 5 Performers (%)
          </Typography>
          <ResponsiveContainer width="100%" height={chartHeight}>
            <BarChart 
              data={topPerformers}
              margin={{ top: 20, right: 30, left: 30, bottom: 5 }}
            >
              <CartesianGrid {...ct.recharts.grid} />
              <XAxis 
                dataKey="ticker"
                axisLine={false}
                tickLine={false}
                tick={false}
              />
              <YAxis 
                tickFormatter={formatYAxis}
                stroke={ct.axis}
                tick={ct.recharts.axis.tick}
              >
                <Label
                  value="Return (%)"
                  position="left"
                  angle={-90}
                  offset={15}
                />
              </YAxis>
              <Tooltip
                formatter={formatTooltip}
                labelStyle={ct.recharts.tooltip.labelStyle}
                itemStyle={ct.recharts.tooltip.itemStyle}
                cursor={ct.recharts.tooltip.cursor}
                contentStyle={{ ...ct.recharts.tooltip.contentStyle, whiteSpace: 'pre-line' }}
              />
              <Bar dataKey="return_pct" name="Return" radius={[4, 4, 0, 0]}>
                {topPerformers.map((p) => (
                  <Cell key={p.ticker} fill={ct.pnlColor(p.return_pct)} />
                ))}
                <LabelList
                  dataKey="ticker"
                  position="bottom"
                  offset={5}
                  fill={ct.text}
                  angle={0}
                  style={{ fontWeight: 'bold' }}
                />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Grid>
        <Grid item xs={12} md={6}>
          <Typography variant="h6" gutterBottom align="center" color="text.primary">
            Bottom 5 Performers (%)
          </Typography>
          <ResponsiveContainer width="100%" height={chartHeight}>
            <BarChart 
              data={bottomPerformers}
              margin={{ top: 20, right: 30, left: 30, bottom: 5 }}
            >
              <CartesianGrid {...ct.recharts.grid} />
              <XAxis 
                dataKey="ticker"
                axisLine={false}
                tickLine={false}
                tick={false}
              />
              <YAxis 
                tickFormatter={formatYAxis}
                stroke={ct.axis}
                tick={ct.recharts.axis.tick}
              >
                <Label
                  value="Return (%)"
                  position="left"
                  angle={-90}
                  offset={15}
                />
              </YAxis>
              <Tooltip
                formatter={formatTooltip}
                labelStyle={ct.recharts.tooltip.labelStyle}
                itemStyle={ct.recharts.tooltip.itemStyle}
                cursor={ct.recharts.tooltip.cursor}
                contentStyle={{ ...ct.recharts.tooltip.contentStyle, whiteSpace: 'pre-line' }}
              />
              <Bar
                dataKey="return_pct"
                fill={ct.down}
                name="Return %"
                radius={[4, 4, 0, 0]}
              >
                <LabelList
                  dataKey="ticker"
                  position="bottom"
                  offset={5}
                  fill={ct.text}
                  angle={0}
                  style={{ fontWeight: 'bold' }}
                />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Grid>
      </Grid>
    </Box>
  );
}
