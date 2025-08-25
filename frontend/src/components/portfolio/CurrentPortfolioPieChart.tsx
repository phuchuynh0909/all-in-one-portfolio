import { Card, CardContent, Typography, Box } from '@mui/material';
import { PieChart } from '@mui/x-charts/PieChart';
import { ChartsTooltipContainer, useItemTooltip } from '@mui/x-charts/ChartsTooltip';

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
              },
            ]}
            slotProps={{
              legend: {
                position: { vertical: 'bottom', horizontal: 'center' },
              },
              tooltip: {
                trigger: 'item',
                renderer: (params) => {
                  const { series, itemData } = params;
                  const data = pieData.find(d => d.id === itemData.id);
                  if (!data) return null;
                  return (
                    <ChartsTooltipContainer>
                      <table style={{ borderSpacing: '8px' }}>
                        <tbody>
                          <tr>
                            <td colSpan={2} style={{ fontWeight: 'bold' }}>
                              {itemData.id}
                            </td>
                          </tr>
                          <tr>
                            <td>Quantity:</td>
                            <td style={{ textAlign: 'right' }}>{Math.round(data.totalQuantity)}</td>
                          </tr>
                          <tr>
                            <td>Market Value:</td>
                            <td style={{ textAlign: 'right' }}>{formatCurrency(data.totalValue)}</td>
                          </tr>
                          <tr>
                            <td>Weight:</td>
                            <td style={{ textAlign: 'right' }}>{(itemData.value * 100).toFixed(1)}%</td>
                          </tr>
                        </tbody>
                      </table>
                    </ChartsTooltipContainer>
                  );
                }
              }
            }}
            height={500}
            margin={{ right: 5 }}
          />
        </Box>
      </CardContent>
    </Card>
  );
}
