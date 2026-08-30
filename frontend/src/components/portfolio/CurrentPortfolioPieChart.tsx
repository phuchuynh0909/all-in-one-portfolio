import { Box } from '@mui/material';
import { PieChart } from '@mui/x-charts/PieChart';
import { useChartTheme } from '../../theme';

interface CurrentPortfolioPieChartProps {
  positions: Array<{ ticker: string; quantity: number; current_price: number }>;
}

export default function CurrentPortfolioPieChart({ positions }: CurrentPortfolioPieChartProps) {
  const ct = useChartTheme();
  const grouped = positions.reduce((acc, pos) => {
    acc[pos.ticker] ??= { ticker: pos.ticker, totalQuantity: 0, totalValue: 0 };
    acc[pos.ticker].totalQuantity += pos.quantity;
    acc[pos.ticker].totalValue += pos.quantity * pos.current_price;
    return acc;
  }, {} as Record<string, { ticker: string; totalQuantity: number; totalValue: number }>);

  const totalValue = Object.values(grouped).reduce((s, p) => s + p.totalValue, 0);

  
  const pieData = Object.values(grouped).map((p, index) => {
    const weight = p.totalValue / totalValue;
    return {
      id: index,
      value: p.totalValue, // Use actual value, not fraction
      // x-charts otherwise falls back to its own palette.
      color: ct.seriesColor(index),
      label: `${p.ticker} (${(weight * 100).toFixed(1)}%)`,
      ticker: p.ticker,
      totalQuantity: p.totalQuantity,
      totalValue: p.totalValue,
    };
  });

  return (
    <Box sx={{ width: '100%', height: 460, p: 1 }}>
          <PieChart
              sx={ct.xChartsSx}
            series={[{
              data: pieData,
              highlightScope: { fade: 'global', highlight: 'item' },
              arcLabel: 'label',
            }]}
            height={440}
            margin={{ right: 5 }}
          />
    </Box>
  );
}