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
      .then(setStocks)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [sectorId, level]);

  return (
    <Box sx={{ height: 400, width: '100%' }}>
      <Typography variant="h6" gutterBottom>
        Stocks in Sector
      </Typography>
      <DataGrid
        rows={stocks}
        columns={columns}
        loading={loading}
        error={error}
        initialState={{
          pagination: { paginationModel: { pageSize: 10 } },
          sorting: {
            sortModel: [{ field: 'vonhoa_d', sort: 'desc' }],
          },
        }}
        pageSizeOptions={[10, 25, 50]}
      />
    </Box>
  );
}
