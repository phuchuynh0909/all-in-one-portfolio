import { useEffect, useState } from 'react';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import { DataGrid } from '@mui/x-data-grid';
import EditIcon from '@mui/icons-material/Edit';
import DeleteIcon from '@mui/icons-material/Delete';
import { apiGet } from '../../lib/api';
import PositionForm from './PositionForm';

type Position = {
  id: number;
  ticker: string;
  quantity: number;
  purchase_price: number;
  purchase_date: string;
  notes: string | null;
};

export default function PositionList() {
  const [positions, setPositions] = useState<Position[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [editPosition, setEditPosition] = useState<Position | undefined>();

  const loadPositions = () => {
    setLoading(true);
    apiGet<Position[]>('/portfolio/positions')
      .then(setPositions)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadPositions();
  }, []);

  const handleEdit = (position: Position) => {
    setEditPosition(position);
    setFormOpen(true);
  };

  const handleDelete = async (id: number) => {
    if (!window.confirm('Are you sure you want to delete this position?')) {
      return;
    }

    try {
      const res = await fetch(
        `${import.meta.env.VITE_API_BASE_URL}/portfolio/positions/${id}`,
        { method: 'DELETE' }
      );
      if (!res.ok) throw new Error(`Error ${res.status}`);
      loadPositions();
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Error deleting position');
    }
  };

  const columns = [
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
      valueFormatter: (params: any) => {
        return Math.floor(params.value); // Format as integer
      },
    },
    {
      field: 'purchase_price',
      headerName: 'Purchase Price',
      flex: 1.2,
      minWidth: 120,
      valueFormatter: (params: any) => {
        return params.value?.toLocaleString('en-US', {
          style: 'currency',
          currency: 'VND',
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
        });
      },
    },
    {
      field: 'purchase_date',
      headerName: 'Purchase Date',
      flex: 1.2,
      minWidth: 120,
    },
    { 
      field: 'notes', 
      headerName: 'Notes', 
      flex: 2,
      minWidth: 150 
    },
    {
      field: 'actions',
      headerName: 'Actions',
      flex: 0.8,
      minWidth: 100,
      renderCell: (params: any) => (
        <Box sx={{ display: 'flex', gap: 1 }}>
          <EditIcon
            sx={{ cursor: 'pointer' }}
            onClick={() => handleEdit(params.row)}
          />
          <DeleteIcon
            sx={{ cursor: 'pointer' }}
            onClick={() => handleDelete(params.row.id)}
          />
        </Box>
      ),
    },
  ];

  return (
    <Box sx={{ height: 400, width: '100%', margin: '0 auto' }}>
      <Box sx={{ mb: 2 }}>
        <Button
          variant="contained"
          onClick={() => {
            setEditPosition(undefined);
            setFormOpen(true);
          }}
        >
          Add Position
        </Button>
      </Box>

      <DataGrid
        rows={positions}
        columns={columns}
        loading={loading}
        initialState={{
          pagination: { pageSize: 10 },
        }}
        pageSizeOptions={[10, 25, 50]}
      />

      <PositionForm
        open={formOpen}
        onClose={() => setFormOpen(false)}
        onSuccess={loadPositions}
        position={editPosition}
        mode={editPosition ? 'edit' : 'create'}
      />
    </Box>
  );
}
