import React from 'react';
import { useNavigate } from 'react-router-dom';
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
  Tooltip,
  TablePagination,
  Chip,
} from '@mui/material';
import SearchIcon from '@mui/icons-material/Search';
import EditIcon from '@mui/icons-material/Edit';
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome';
import type { Report, RagStatus } from '../../lib/services/report';
import { format } from 'date-fns';

type ChipColor = 'default' | 'info' | 'success' | 'error' | 'warning';

const RAG_META: Record<string, { label: string; color: ChipColor; spinning?: boolean }> = {
  PENDING: { label: 'Queued', color: 'info', spinning: true },
  PARSING: { label: 'Parsing', color: 'info', spinning: true },
  PARSED: { label: 'Parsed', color: 'default' },
  SUMMARIZING: { label: 'Summarizing', color: 'info', spinning: true },
  EMBEDDING: { label: 'Embedding', color: 'info', spinning: true },
  EMBEDDED: { label: 'Embedded', color: 'success' },
  FAILED: { label: 'Failed', color: 'error' },
};

const IN_PROGRESS = ['PENDING', 'PARSING', 'SUMMARIZING', 'EMBEDDING'];

interface ReportTableProps {
  reports: Report[];
  isLoading: boolean;
  onSymbolSearch: (symbol: string) => void;
  ragStatuses?: Record<number, RagStatus>;
  onEmbed?: (reportId: number) => void;
}

export const ReportTable: React.FC<ReportTableProps> = ({
  reports,
  isLoading,
  onSymbolSearch,
  ragStatuses = {},
  onEmbed,
}) => {
  const [searchSymbol, setSearchSymbol] = React.useState('');
  const [page, setPage] = React.useState(0);
  const [rowsPerPage, setRowsPerPage] = React.useState(10);
  const navigate = useNavigate();

  // Reset to first page whenever the underlying data set changes
  React.useEffect(() => {
    setPage(0);
  }, [reports]);

  const handleChangePage = (_event: unknown, newPage: number) => {
    setPage(newPage);
  };

  const handleChangeRowsPerPage = (event: React.ChangeEvent<HTMLInputElement>) => {
    setRowsPerPage(parseInt(event.target.value, 10));
    setPage(0);
  };

  const paginatedReports = reports.slice(page * rowsPerPage, page * rowsPerPage + rowsPerPage);

  const handleSearch = () => {
    onSymbolSearch(searchSymbol);
  };

  const handleKeyPress = (event: React.KeyboardEvent) => {
    if (event.key === 'Enter') {
      handleSearch();
    }
  };

  const handleEdit = (reportId: number) => {
    navigate(`/report/${reportId}`);
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
                <TableCell>RAG</TableCell>
                <TableCell align="center">Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {paginatedReports.map((report) => {
                const st = ragStatuses[report.id];
                const meta = st ? RAG_META[st.status] : undefined;
                const inProgress = !!st && IN_PROGRESS.includes(st.status);
                return (
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
                  <TableCell>
                    {meta ? (
                      <Tooltip title={st?.error || meta.label}>
                        <Chip
                          size="small"
                          variant="outlined"
                          color={meta.color}
                          icon={inProgress ? <CircularProgress size={12} /> : undefined}
                          label={
                            st?.status === 'EMBEDDED' && st?.chunk_count
                              ? `Embedded · ${st.chunk_count}`
                              : meta.label
                          }
                        />
                      </Tooltip>
                    ) : (
                      <Typography variant="caption" color="text.secondary">
                        —
                      </Typography>
                    )}
                  </TableCell>
                  <TableCell align="center">
                    <Tooltip title="Edit Summary">
                      <IconButton
                        size="small"
                        color="primary"
                        onClick={() => handleEdit(report.id)}
                      >
                        <EditIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                    <Tooltip title={st?.status === 'EMBEDDED' ? 'Re-embed (RAG)' : 'Embed for RAG'}>
                      <span>
                        <IconButton
                          size="small"
                          color="secondary"
                          disabled={inProgress || !onEmbed}
                          onClick={() => onEmbed?.(report.id)}
                        >
                          {inProgress ? (
                            <CircularProgress size={16} />
                          ) : (
                            <AutoAwesomeIcon fontSize="small" />
                          )}
                        </IconButton>
                      </span>
                    </Tooltip>
                  </TableCell>
                </TableRow>
                );
              })}
            </TableBody>
          </Table>
          <TablePagination
            component="div"
            count={reports.length}
            page={page}
            onPageChange={handleChangePage}
            rowsPerPage={rowsPerPage}
            onRowsPerPageChange={handleChangeRowsPerPage}
            rowsPerPageOptions={[10, 25, 50, 100]}
          />
        </TableContainer>
      )}
    </Box>
  );
};
