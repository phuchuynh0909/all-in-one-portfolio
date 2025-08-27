import { useEffect, useState } from 'react';
import {
  DataGrid,
  GridColDef,
  GridValueFormatterParams,
} from '@mui/x-data-grid';
import { Box, Typography } from '@mui/material';
import { apiGet } from '../../lib/api';

type Stock = {
  id: number;
  symbol: string;
  name: string;
  vonhoa_d: number;
};

const columns: GridColDef[] = [
  { field: 'symbol', headerName: 'Symbol', width: 120 },
  { field: 'name', headerName: 'Name', width: 200 },
  {
    field: 'vonhoa_d',
    headerName: 'Market Cap (B)',
    width: 150,
    valueFormatter: (params: GridValueFormatterParams<number>) => {
      return params.value?.toLocaleString('en-US', {
        maximumFractionDigits: 0,
      });
    },
  },
];

export default function StockList({
  sectorId,
  level,
}: {
  sectorId: number;
  level: number;
}) {
  const [stocks, setStocks] = useState<Stock[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!sectorId) return;

    setLoading(true);
    apiGet<Stock[]>(`/sector/symbols/${level}/${sectorId}`)
      .then((data) => {
        // Filter out stocks with null, undefined, or zero market cap
        const filteredStocks = data.filter(stock => 
          stock.vonhoa_d && 
          stock.vonhoa_d > 0 && 
          !isNaN(stock.vonhoa_d)
        );
        
        // Sort by market cap in descending order
        const sortedStocks = filteredStocks.sort((a, b) => b.vonhoa_d - a.vonhoa_d);
        
        setStocks(sortedStocks);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [sectorId, level]);

  return (
    <Box sx={{ height: 500, width: '100%' }}>
      <Typography variant="h6" gutterBottom>
        Stocks in Sector
      </Typography>
      <DataGrid
        sx={{ mt: 10 }}
        rows={stocks}
        columns={columns}
        loading={loading}
        error={error}
        initialState={{
          pagination: { paginationModel: { pageSize: 10 } },
        }}
        pageSizeOptions={[10, 25, 50]}
      />
    </Box>
  );
}
