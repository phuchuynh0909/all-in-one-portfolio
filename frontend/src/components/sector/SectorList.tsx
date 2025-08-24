import { useEffect, useState } from 'react';
import {
  DataGrid,
  GridColDef,
  GridValueFormatterParams,
} from '@mui/x-data-grid';
import {
  Box,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Typography,
} from '@mui/material';
import { apiGet } from '../../lib/api';

type Sector = {
  id: number;
  level: number;
  type: string;
  name: string;
  smg: number;
  dif: number;
  dif_w: number;
  dif_m: number;
  dif_3m: number;
  vonhoa_d: number;
  eps_d: number;
  pe_d: number;
  pb_d: number;
  roa_ttm: number;
  roe_ttm: number;
  lnst_yoy_ttm: number;
};

const columns: GridColDef[] = [
  { field: 'name', headerName: 'Name', width: 200 },
  { field: 'type', headerName: 'Type', width: 120 },
  {
    field: 'smg',
    headerName: 'SMG',
    width: 120,
    valueFormatter: (params: GridValueFormatterParams<number>) => {
      return params.value;
    },
  },
  {
    field: 'dif',
    headerName: 'DIF',
    width: 120,
    valueFormatter: (params: GridValueFormatterParams<number>) => {
      return params.value;
    },
  },
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
  {
    field: 'pe_d',
    headerName: 'P/E',
    width: 120,
    valueFormatter: (params: GridValueFormatterParams<number>) => {
      return params.value;
    },
  },
  {
    field: 'pb_d',
    headerName: 'P/B',
    width: 120,
    valueFormatter: (params: GridValueFormatterParams<number>) => {
      return params.value;
    },
  },
  {
    field: 'roe_ttm',
    headerName: 'ROE',
    width: 120,
    valueFormatter: (params: GridValueFormatterParams<number>) => {
      return params.value ? `${params.value}%` : '';
    },
  },
];

export default function SectorList() {
  const [sectors, setSectors] = useState<Sector[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [level, setLevel] = useState<number>(3);

  const loadSectors = (selectedLevel: number) => {
    setLoading(true);
    apiGet<Sector[]>(`/sector/list/${selectedLevel}`)
      .then(setSectors)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadSectors(level);
  }, [level]);

  return (
    <Box sx={{ height: 600, width: '100%' }}>
      <Box sx={{ mb: 3, display: 'flex', gap: 2, alignItems: 'center' }}>
        <Typography variant="h6">Sector Analysis</Typography>
        <FormControl size="small" sx={{ minWidth: 120 }}>
          <InputLabel>Level</InputLabel>
          <Select
            value={level}
            label="Level"
            onChange={(e) => setLevel(Number(e.target.value))}
          >
            <MenuItem value={3}>Level 3</MenuItem>
            <MenuItem value={4}>Level 4</MenuItem>
          </Select>
        </FormControl>
      </Box>

      <DataGrid
        rows={sectors}
        columns={columns}
        loading={loading}
        error={error}
        initialState={{
          pagination: { paginationModel: { pageSize: 25 } },
          sorting: {
            sortModel: [{ field: 'vonhoa_d', sort: 'desc' }],
          },
        }}
        pageSizeOptions={[25, 50, 100]}
      />
    </Box>
  );
}
