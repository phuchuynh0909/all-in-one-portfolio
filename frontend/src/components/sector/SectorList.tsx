import { useEffect, useState } from 'react';
import {
  DataGrid,
  type GridColDef,
  type GridValueFormatterParams,
} from '@mui/x-data-grid';
import {
  Box,
  Typography,
  TextField,
  InputAdornment,
} from '@mui/material';
import SearchIcon from '@mui/icons-material/Search';
import { apiGet } from '../../lib/api';

// Function to remove Vietnamese diacritics
const removeDiacritics = (str: string): string => {
  return str
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '') // Remove combining diacritical marks
    .replace(/đ/g, 'd')
    .replace(/Đ/g, 'D');
};

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

export default function SectorList({ 
  level, 
  onSectorSelect 
}: { 
  level: number;
  onSectorSelect?: (sectorId: number) => void;
}) {
  const [sectors, setSectors] = useState<Sector[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState<string>('');

  const loadSectors = (selectedLevel: number) => {
    setLoading(true);
    apiGet<Sector[]>(`/sector/list/${selectedLevel}`)
      .then(setSectors)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadSectors(level);
    setSearchTerm(''); // Clear search when level changes
  }, [level]);

  // Filter sectors based on search term (supports Vietnamese diacritics)
  const filteredSectors = sectors.filter(sector => {
    const searchLower = searchTerm.toLowerCase();
    const searchNoDiacritics = removeDiacritics(searchLower);
    
    const nameLower = sector.name.toLowerCase();
    const nameNoDiacritics = removeDiacritics(nameLower);
    
    const typeLower = sector.type.toLowerCase();
    const typeNoDiacritics = removeDiacritics(typeLower);
    
    return (
      // Search with diacritics
      nameLower.includes(searchLower) ||
      typeLower.includes(searchLower) ||
      // Search without diacritics
      nameNoDiacritics.includes(searchNoDiacritics) ||
      typeNoDiacritics.includes(searchNoDiacritics)
    );
  });

  return (
    <Box sx={{ height: 500, width: '100%' }}>
      <Box sx={{ mb: 3 }}>
        <Typography variant="h6">
          Sectors (Level {level})
          {searchTerm && (
            <Typography component="span" variant="body2" sx={{ ml: 1, color: 'text.secondary' }}>
              - {filteredSectors.length} of {sectors.length} shown
            </Typography>
          )}
        </Typography>
        <TextField
          size="small"
          placeholder="Search by name or type..."
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
          sx={{ mt: 2, width: '100%' }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon />
              </InputAdornment>
            ),
          }}
        />
      </Box>

      <DataGrid
        rows={filteredSectors}
        columns={columns}
        loading={loading}
        error={error}
        initialState={{
          sorting: {
            sortModel: [{ field: 'vonhoa_d', sort: 'desc' }],
          },
        }}
        onRowClick={(params) => {
          if (onSectorSelect) {
            onSectorSelect(params.row.id);
          }
        }}
        sx={{
          '& .MuiDataGrid-row:hover': {
            cursor: 'pointer',
          },
        }}
      />
    </Box>
  );
}
