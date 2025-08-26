import { useState } from 'react';
import { Box, Button, Chip } from '@mui/material';
import { DataGrid, GridColDef } from '@mui/x-data-grid';
import ClosePositionDialog from './ClosePositionDialog';

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
  onPositionUpdate?: () => void;
};

export default function PositionsTable({ positions, onPositionUpdate }: PositionsTableProps) {
  const [closeDialogOpen, setCloseDialogOpen] = useState(false);
  const [selectedPosition, setSelectedPosition] = useState<Position | null>(null);

  const handleClosePosition = (position: Position) => {
    setSelectedPosition(position);
    setCloseDialogOpen(true);
  };

  const handleCloseDialogClose = () => {
    setCloseDialogOpen(false);
    setSelectedPosition(null);
  };

  const handleCloseSuccess = () => {
    // Refresh positions after successful close
    if (onPositionUpdate) {
      onPositionUpdate();
    }
  };

  // Calculate P/L for display
  const positionsWithPL = positions.map((position) => {
    const unrealizedPL = (position.current_price - position.purchase_price) * position.quantity;
    const unrealizedPLPct = ((position.current_price / position.purchase_price) - 1) * 100;
    
    return {
      ...position,
      unrealized_pl: unrealizedPL,
      unrealized_pl_pct: unrealizedPLPct,
    };
  });

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
      valueFormatter: (params) => {
        const value = Math.round((params.value || 0) * 100) / 100;
        return value
      },
    },
    {
      field: 'current_price',
      headerName: 'Current Price',
      flex: 1.2,
      minWidth: 120,
      valueFormatter: (params) => {
        const value = Math.round((params.value || 0) * 100) / 100;
        return value
      },
    },
    {
      field: 'unrealized_pl',
      headerName: 'Unrealized P/L',
      flex: 1.3,
      minWidth: 130,
      renderCell: (params) => {
        const value = Math.round((params.value as number || 0) * 100) / 100;
        const pct = Math.round((params.row.unrealized_pl_pct as number || 0) * 100) / 100;
        const color = value >= 0 ? 'success' : 'error';
        
        return (
          <Chip
            label={`${value.toLocaleString('en-US', {
              style: 'currency',
              currency: 'VND',
              minimumFractionDigits: 2,
              maximumFractionDigits: 2,
            })} (${pct.toFixed(2)}%)`}
            color={color}
            variant="outlined"
            size="small"
          />
        );
      },
    },
    {
      field: 'purchase_date',
      headerName: 'Purchase Date',
      flex: 1.2,
      minWidth: 120,
      valueFormatter: (params) => new Date(params.value).toLocaleDateString(),
    },
    {
      field: 'actions',
      headerName: 'Actions',
      flex: 1,
      minWidth: 120,
      sortable: false,
      renderCell: (params) => (
        <Button
          variant="outlined"
          color="warning"
          size="small"
          onClick={() => handleClosePosition(params.row as Position)}
          sx={{ minWidth: 80 }}
        >
          Close
        </Button>
      ),
    },
  ];

  return (
    <>
      <Box sx={{ height: 400, width: '95%', margin: '0 auto' }}>
        <DataGrid
          rows={positionsWithPL}
          columns={columns}
          initialState={{
            pagination: { pageSize: 10 },
            sorting: {
              sortModel: [{ field: 'purchase_date', sort: 'desc' }],
            },
          }}
          pageSizeOptions={[10, 25, 50]}
          disableRowSelectionOnClick
        />
      </Box>

      <ClosePositionDialog
        open={closeDialogOpen}
        position={selectedPosition}
        onClose={handleCloseDialogClose}
        onSuccess={handleCloseSuccess}
      />
    </>
  );
}
