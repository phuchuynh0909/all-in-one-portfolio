import { Card, CardContent, Typography, Box } from '@mui/material';
import { PieChart } from '@mui/x-charts/PieChart';

interface CurrentPortfolioPieChartProps {
  positions: Array<{
    ticker: string;
    quantity: number;
    current_price: number;
  }>;
}

export default function CurrentPortfolioPieChart({ positions }: CurrentPortfolioPieChartProps) {
  // Group positions by ticker and calculate total value for each
  const groupedPositions = positions.reduce((acc, pos) => {
    if (!acc[pos.ticker]) {
      acc[pos.ticker] = {
        ticker: pos.ticker,
        totalQuantity: 0,
        totalValue: 0,
      };
    }
    acc[pos.ticker].totalQuantity += pos.quantity;
    acc[pos.ticker].totalValue += pos.quantity * pos.current_price;
    return acc;
  }, {} as Record<string, { ticker: string; totalQuantity: number; totalValue: number; }>);

  const totalValue = Object.values(groupedPositions).reduce((sum, pos) => sum + pos.totalValue, 0);
  
  const formatCurrency = (value: number) => {
    return value.toLocaleString('vi-VN', {
      style: 'currency',
      currency: 'VND',
      minimumFractionDigits: 0,
      maximumFractionDigits: 0,
    });
  };

  const pieData = Object.values(groupedPositions).map(pos => {
    const value = pos.totalValue / totalValue;
    return {
      id: pos.ticker,
      value,
      label: `${pos.ticker} (${(value * 100).toFixed(1)}%)`,
      totalQuantity: pos.totalQuantity,
      totalValue: pos.totalValue,
    };
  });

  return (
    <Card>
      <CardContent>
        <Typography variant="h6" gutterBottom>
          Current Portfolio Weights
        </Typography>
        <Box sx={{ width: '100%', height: 500 }}>
          <PieChart
            series={[
              {
                data: pieData,
                highlightScope: { fade: 'global', highlight: 'item' },
                arcLabel: 'label',
                tooltip: {
                  trigger: 'item',
                },
              },
            ]}
            tooltip={{
              formatter: (item: any) => {
                const data = pieData.find(d => d.id === item.id);
                if (!data) return item.label;
                return {
                  title: `${item.id} (${(item.value * 100).toFixed(1)}%)`,
                  body: [
                    `-----------------------`,
                    `Quantity: ${Math.round(data.totalQuantity)}`,
                    `Market Value: ${formatCurrency(data.totalValue)}`,
                    `Weight: ${(item.value * 100).toFixed(1)}%`
                  ].join('\n')
                };
              }
            }}
            height={500}
            margin={{ right: 5 }}
            slotProps={{
              legend: {

                position: { vertical: 'bottom', horizontal: 'center' },
              },
            }}
          />
        </Box>
      </CardContent>
    </Card>
  );
}
