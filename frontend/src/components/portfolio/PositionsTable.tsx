import { Box } from '@mui/material';
import { DataGrid, GridColDef } from '@mui/x-data-grid';

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
      field: 'purchase_price',
      headerName: 'Purchase Price',
      flex: 1.2,
      minWidth: 120,
      valueFormatter: (params) => params.value?.toLocaleString('en-US', {
        style: 'currency',
        currency: 'VND',
        maximumFractionDigits: 0,
      }),
    },
    {
      field: 'current_price',
      headerName: 'Current Price',
      flex: 1.2,
      minWidth: 120,
      valueFormatter: (params) => params.value?.toLocaleString('en-US', {
        style: 'currency',
        currency: 'VND',
        maximumFractionDigits: 0,
      }),
    },
    {
      field: 'purchase_date',
      headerName: 'Purchase Date',
      flex: 1.2,
      minWidth: 120,
      valueFormatter: (params) => new Date(params.value).toLocaleDateString(),
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
            sortModel: [{ field: 'purchase_date', sort: 'desc' }],
          },
        }}
        pageSizeOptions={[10, 25, 50]}
      />
    </Box>
  );
}
