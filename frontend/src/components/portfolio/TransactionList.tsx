import { useEffect, useState } from 'react';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import { DataGrid, GridToolbar } from '@mui/x-data-grid';
import type { GridColDef } from '@mui/x-data-grid';
import EditIcon from '@mui/icons-material/Edit';
import DeleteIcon from '@mui/icons-material/Delete';
import { apiGet } from '../../lib/api';
import TransactionForm from './TransactionForm';

interface Transaction {
  id: number;
  ticker: string;
  transaction_type: 'buy' | 'sell';
  quantity: number;
  price: number;
  close_price?: number;
  transaction_date: string;
  fees: number;
  notes?: string;
}

export default function TransactionList() {
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [editTransaction, setEditTransaction] = useState<Transaction | undefined>();

  const loadTransactions = () => {
    setLoading(true);
    apiGet<Transaction[]>('/portfolio/transactions')
      .then(setTransactions)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadTransactions();
  }, []);

  const handleEdit = (transaction: Transaction) => {
    setEditTransaction(transaction);
    setFormOpen(true);
  };

  const handleDelete = async (id: number) => {
    if (!window.confirm('Are you sure you want to delete this transaction?')) {
      return;
    }

    try {
      const res = await fetch(
        `${import.meta.env.VITE_API_BASE_URL}/portfolio/transactions/${id}`,
        { method: 'DELETE' }
      );
      if (!res.ok) throw new Error(`Error ${res.status}`);
      loadTransactions();
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Error deleting transaction');
    }
  };

  const columns: GridColDef[] = [
    { 
      field: 'ticker', 
      headerName: 'Ticker', 
      flex: 1,
      minWidth: 100 
    },
    {
      field: 'transaction_type',
      headerName: 'Type',
      flex: 0.8,
      minWidth: 80,
      valueFormatter: (params: any) => {
        return params.value?.toUpperCase();
      },
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
      field: 'price',
      headerName: 'Price',
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
      field: 'close_price',
      headerName: 'Close Price',
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
      field: 'transaction_date',
      headerName: 'Date',
      flex: 1.2,
      minWidth: 120,
    },
    {
      field: 'fees',
      headerName: 'Fees',
      flex: 1,
      minWidth: 100,
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
      field: 'notes', 
      headerName: 'Notes', 
      flex: 2,
      minWidth: 150 
    },
    {
      field: 'actions',
      headerName: 'Actions',
      flex: 0.8,
      minWidth: 80,
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
    <Box sx={{ width: '100%' }}>
      <Box sx={{ mb: 2 }}>
        <Button
          variant="contained"
          onClick={() => {
            setEditTransaction(undefined);
            setFormOpen(true);
          }}
        >
          Add Transaction
        </Button>
      </Box>

      <DataGrid
        rows={transactions}
        columns={columns}
        loading={loading}
        autoHeight
        initialState={{
          sorting: {
            sortModel: [{ field: 'transaction_date', sort: 'desc' }],
          },
        }}
        rowsPerPageOptions={[10, 25, 50, 100]}
        pageSize={25}
        onPageChange={(page) => {
          console.log('Page changed:', page);
        }}
        components={{
          Toolbar: GridToolbar,
        }}
        componentsProps={{
          toolbar: {
            showQuickFilter: true,
            quickFilterProps: { debounceMs: 500 },
          },
        }}
        sx={{
          '& .MuiDataGrid-toolbarContainer': {
            padding: 2,
            backgroundColor: 'background.paper',
            borderBottom: 1,
            borderColor: 'divider',
          },
        }}
      />

      <TransactionForm
        open={formOpen}
        onClose={() => setFormOpen(false)}
        onSuccess={loadTransactions}
        transaction={editTransaction}
        mode={editTransaction ? 'edit' : 'create'}
      />
    </Box>
  );
}
