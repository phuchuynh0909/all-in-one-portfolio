import { useEffect, useState } from 'react';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import { DataGrid } from '@mui/x-data-grid';
import EditIcon from '@mui/icons-material/Edit';
import DeleteIcon from '@mui/icons-material/Delete';
import { API_BASE_URL, apiGet } from '../../lib/api';
import PositionForm from './PositionForm';
import type { Position } from '../../lib/services/portfolio';
import { ErrorState } from '../ui';

type PositionListProps = {
  /** Called after a position is created, edited, or deleted here — lets a
   * parent page that shows its own copy of positions (summary cards,
   * holdings table) know it should refetch too. */
  onDataChanged?: () => void;
};

export default function PositionList({ onDataChanged }: PositionListProps) {
  const [positions, setPositions] = useState<Position[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pageSize, setPageSize] = useState(10);
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
        `${API_BASE_URL}/portfolio/positions/${id}`,
        { method: 'DELETE' }
      );
      if (!res.ok) throw new Error(`Error ${res.status}`);
      loadPositions();
      onDataChanged?.();
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
        return Math.round(params.value * 100) / 100;
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
      {error && <ErrorState error={error} title="Could not load positions" onRetry={loadPositions} />}
      <DataGrid
        rows={positions}
        columns={columns}
        loading={loading}
        pageSize={pageSize}
        onPageSizeChange={setPageSize}
        rowsPerPageOptions={[10, 25, 50]}
      />

      <Box sx={{ mt: 2 }}>
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

      <PositionForm
        open={formOpen}
        onClose={() => setFormOpen(false)}
        onSuccess={() => { loadPositions(); onDataChanged?.(); }}
        position={editPosition}
        mode={editPosition ? 'edit' : 'create'}
      />
    </Box>
  );
}
