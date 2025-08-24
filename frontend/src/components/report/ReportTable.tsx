import React from 'react';
import {
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  Link,
  Box,
  TextField,
  IconButton,
  Typography,
  CircularProgress,
} from '@mui/material';
import SearchIcon from '@mui/icons-material/Search';
import { Report } from '../../lib/services/report';
import { format } from 'date-fns';

interface ReportTableProps {
  reports: Report[];
  isLoading: boolean;
  onSymbolSearch: (symbol: string) => void;
}

export const ReportTable: React.FC<ReportTableProps> = ({ reports, isLoading, onSymbolSearch }) => {
  const [searchSymbol, setSearchSymbol] = React.useState('');

  const handleSearch = () => {
    onSymbolSearch(searchSymbol);
  };

  const handleKeyPress = (event: React.KeyboardEvent) => {
    if (event.key === 'Enter') {
      handleSearch();
    }
  };

  return (
    <Box>
      <Box sx={{ mb: 2, display: 'flex', alignItems: 'center', gap: 2 }}>
        <TextField
          label="Symbol"
          value={searchSymbol}
          onChange={(e) => setSearchSymbol(e.target.value.toUpperCase())}
          onKeyPress={handleKeyPress}
          size="small"
        />
        <IconButton onClick={handleSearch} color="primary">
          <SearchIcon />
        </IconButton>
      </Box>

      {isLoading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', p: 3 }}>
          <CircularProgress />
        </Box>
      ) : reports.length === 0 ? (
        <Typography variant="body1" sx={{ textAlign: 'center', p: 3 }}>
          No reports found
        </Typography>
      ) : (
        <TableContainer component={Paper}>
          <Table sx={{ minWidth: 650 }} size="small">
            <TableHead>
              <TableRow>
                <TableCell>ID</TableCell>
                <TableCell>Symbol</TableCell>
                <TableCell>Report Name</TableCell>
                <TableCell>Source</TableCell>
                <TableCell>Date</TableCell>
                <TableCell>Sector</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {reports.map((report) => (
                <TableRow key={report.id} hover>
                  <TableCell>{report.id}</TableCell>
                  <TableCell>{report.mack}</TableCell>
                  <TableCell>
                    <Link href={report.url} target="_blank" rel="noopener noreferrer">
                      {report.tenbaocao}
                    </Link>
                  </TableCell>
                  <TableCell>{report.nguon}</TableCell>
                  <TableCell>
                    {report.ngaykn ? format(new Date(report.ngaykn), 'dd/MM/yyyy') : ''}
                  </TableCell>
                  <TableCell>{report.rsnganh}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Box>
  );
};
