import { Box } from '@mui/material';
import { DataGrid, GridColDef } from '@mui/x-data-grid';

type Position = {
  id: number;
  ticker: string;
  quantity: number;
  cost_basis: number;
  current_value: number;
  unrealized_gain: number;
  return_pct: number;
};

type PositionsTableProps = {
  positions: Position[];
};

export default function PositionsTable({ positions }: PositionsTableProps) {
  const columns: GridColDef[] = [
    { 
      field: 'ticker', 
      headerName: 'Ticker', 
      flex: 1,
      minWidth: 100 
    },
    {
      field: 'quantity',
      headerName: 'Quantity',
      flex: 1,
      minWidth: 100,
      valueFormatter: (params) => Math.floor(params.value),
    },
    {
      field: 'cost_basis',
      headerName: 'Cost Basis',
      flex: 1.2,
      minWidth: 120,
      valueFormatter: (params) => params.value?.toLocaleString('en-US', {
        style: 'currency',
        currency: 'VND',
        maximumFractionDigits: 0,
      }),
    },
    {
      field: 'current_value',
      headerName: 'Current Value',
      flex: 1.2,
      minWidth: 120,
      valueFormatter: (params) => params.value?.toLocaleString('en-US', {
        style: 'currency',
        currency: 'VND',
        maximumFractionDigits: 0,
      }),
    },
    {
      field: 'unrealized_gain',
      headerName: 'Unrealized Gain',
      flex: 1.2,
      minWidth: 120,
      valueFormatter: (params) => params.value?.toLocaleString('en-US', {
        style: 'currency',
        currency: 'VND',
        maximumFractionDigits: 0,
      }),
    },
    {
      field: 'return_pct',
      headerName: 'Return %',
      flex: 1,
      minWidth: 100,
      valueFormatter: (params) => `${params.value?.toFixed(2)}%`,
    },
  ];

  return (
    <Box sx={{ height: 400, width: '95%', margin: '0 auto' }}>
      <DataGrid
        rows={positions}
        columns={columns}
        initialState={{
          pagination: { pageSize: 10 },
          sorting: {
            sortModel: [{ field: 'return_pct', sort: 'desc' }],
          },
        }}
        pageSizeOptions={[10, 25, 50]}
      />
    </Box>
  );
}
