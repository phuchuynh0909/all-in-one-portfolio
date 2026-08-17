import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Box,
  Chip,
  CircularProgress,
  Divider,
  IconButton,
  InputAdornment,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from '@mui/material';
import SearchIcon from '@mui/icons-material/Search';
import RefreshIcon from '@mui/icons-material/Refresh';
import {
  BlockEpisode,
  CANDIDATE_TYPE_SHORT,
  CandidateType,
  fetchBlockEpisodes,
} from '../../lib/services/blockEpisodes';

const BUY = '#26a69a';
const SELL = '#ef5350';

const sideColor = (side: number) => (side === 1 ? BUY : side === 2 ? SELL : '#9e9e9e');

/** Compact VND notional: 1.2B / 340M / 12K. */
function fmtValue(v: number): string {
  const a = Math.abs(v);
  if (a >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${(v / 1e3).toFixed(0)}K`;
  return v.toFixed(0);
}

/** Episode start as ICT (Asia/Ho_Chi_Minh) HH:MM:SS. */
function fmtTime(epoch: number): string {
  return new Date(epoch * 1000).toLocaleTimeString('en-GB', {
    timeZone: 'Asia/Ho_Chi_Minh',
    hour12: false,
  });
}

function fmtDate(epoch: number): string {
  return new Date(epoch * 1000).toLocaleDateString('en-CA', {
    timeZone: 'Asia/Ho_Chi_Minh',
  });
}

type TypeFilter = 'ALL' | CandidateType;
type SideFilter = 'ALL' | 1 | 2;

const cellSx = { py: 0.6, px: 1, borderColor: 'rgba(255,255,255,0.08)' } as const;
const headSx = { ...cellSx, color: 'text.secondary', fontWeight: 700, fontSize: 12 } as const;

export default function BlockEpisodesPanel() {
  const [symbolInput, setSymbolInput] = useState('FPT');
  const [symbol, setSymbol] = useState('FPT');
  const [typeFilter, setTypeFilter] = useState<TypeFilter>('ALL');
  const [sideFilter, setSideFilter] = useState<SideFilter>('ALL');

  const { data, isFetching, isError, refetch, dataUpdatedAt } = useQuery({
    queryKey: ['blockEpisodes', symbol, typeFilter, sideFilter],
    queryFn: () =>
      fetchBlockEpisodes(symbol, {
        candidateType: typeFilter === 'ALL' ? undefined : typeFilter,
        side: sideFilter === 'ALL' ? undefined : sideFilter,
        limit: 500,
      }),
    enabled: !!symbol,
    refetchInterval: 20_000,
  });

  const episodes: BlockEpisode[] = useMemo(
    () => (data?.episodes ?? []).slice().reverse(), // newest first
    [data],
  );

  const totals = useMemo(() => {
    let buy = 0;
    let sell = 0;
    for (const e of data?.episodes ?? []) {
      if (e.side === 1) buy += e.abs_notional;
      else if (e.side === 2) sell += e.abs_notional;
    }
    return { buy, sell };
  }, [data]);

  const applySymbol = () => {
    const s = symbolInput.trim().toUpperCase();
    if (s) setSymbol(s);
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Header */}
      <Box sx={{ p: 1.5, pb: 1 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 700, flexGrow: 1 }}>
            Block Episodes
          </Typography>
          <Tooltip title="Refresh">
            <span>
              <IconButton size="small" onClick={() => refetch()} disabled={isFetching}>
                <RefreshIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
        </Box>

        <TextField
          size="small"
          fullWidth
          value={symbolInput}
          onChange={(e) => setSymbolInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && applySymbol()}
          placeholder="Symbol e.g. FPT"
          InputProps={{
            endAdornment: (
              <InputAdornment position="end">
                <IconButton size="small" onClick={applySymbol}>
                  <SearchIcon fontSize="small" />
                </IconButton>
              </InputAdornment>
            ),
          }}
        />

        <Box sx={{ display: 'flex', gap: 1, mt: 1, flexWrap: 'wrap' }}>
          <ToggleButtonGroup
            size="small"
            exclusive
            value={typeFilter}
            onChange={(_, v) => v && setTypeFilter(v)}
          >
            <ToggleButton value="ALL" sx={{ px: 1, py: 0.2, fontSize: 11 }}>All</ToggleButton>
            <ToggleButton value="FLOW_CLUSTER" sx={{ px: 1, py: 0.2, fontSize: 11 }}>Flow</ToggleButton>
            <ToggleButton value="LARGE_PRINT" sx={{ px: 1, py: 0.2, fontSize: 11 }}>Large</ToggleButton>
            <ToggleButton value="FLOW_CLUSTER_AND_LARGE_PRINT" sx={{ px: 1, py: 0.2, fontSize: 11 }}>Both</ToggleButton>
          </ToggleButtonGroup>
          <ToggleButtonGroup
            size="small"
            exclusive
            value={sideFilter}
            onChange={(_, v) => v != null && setSideFilter(v)}
          >
            <ToggleButton value="ALL" sx={{ px: 1, py: 0.2, fontSize: 11 }}>All</ToggleButton>
            <ToggleButton value={1} sx={{ px: 1, py: 0.2, fontSize: 11, color: BUY }}>Buy</ToggleButton>
            <ToggleButton value={2} sx={{ px: 1, py: 0.2, fontSize: 11, color: SELL }}>Sell</ToggleButton>
          </ToggleButtonGroup>
        </Box>
      </Box>

      <Divider />

      {/* Table */}
      <Box sx={{ flexGrow: 1, overflow: 'auto' }}>
        {isError ? (
          <Typography sx={{ p: 2, color: 'error.main', fontSize: 13 }}>
            Failed to load episodes.
          </Typography>
        ) : (
          <Table size="small" stickyHeader>
            <TableHead>
              <TableRow>
                <TableCell sx={headSx}>Time</TableCell>
                <TableCell sx={headSx} align="center">M/B</TableCell>
                <TableCell sx={headSx}>Type</TableCell>
                <TableCell sx={headSx} align="right">Value</TableCell>
                <TableCell sx={headSx} align="right">Trd</TableCell>
                <TableCell sx={headSx} align="right">z</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {episodes.map((e) => (
                <TableRow key={`${e.start_epoch}-${e.side}`} hover>
                  <Tooltip
                    title={`${fmtDate(e.start_epoch)} ${fmtTime(e.start_epoch)}–${fmtTime(e.end_epoch)} · ${e.duration_seconds}s · ${e.num_bins} bins · imb ${e.max_abs_imbalance.toFixed(2)} · ${e.large_print_count} large`}
                    placement="left"
                  >
                    <TableCell sx={{ ...cellSx, fontVariantNumeric: 'tabular-nums' }}>
                      {fmtTime(e.start_epoch)}
                    </TableCell>
                  </Tooltip>
                  <TableCell sx={cellSx} align="center">
                    <Typography component="span" sx={{ color: sideColor(e.side), fontWeight: 700, fontSize: 13 }}>
                      {e.side === 1 ? 'M' : e.side === 2 ? 'B' : '–'}
                    </Typography>
                  </TableCell>
                  <TableCell sx={cellSx}>
                    <Chip
                      label={CANDIDATE_TYPE_SHORT[e.candidate_type] ?? e.candidate_type}
                      size="small"
                      sx={{
                        height: 18,
                        fontSize: 10,
                        bgcolor: 'rgba(255,255,255,0.08)',
                        color: 'text.secondary',
                      }}
                    />
                  </TableCell>
                  <TableCell sx={{ ...cellSx, color: sideColor(e.side), fontWeight: 600, fontVariantNumeric: 'tabular-nums' }} align="right">
                    {fmtValue(e.abs_notional)}
                  </TableCell>
                  <TableCell sx={{ ...cellSx, fontVariantNumeric: 'tabular-nums' }} align="right">
                    {e.num_trades}
                  </TableCell>
                  <TableCell sx={{ ...cellSx, fontVariantNumeric: 'tabular-nums' }} align="right">
                    {e.max_abs_z.toFixed(1)}
                  </TableCell>
                </TableRow>
              ))}
              {!isFetching && episodes.length === 0 && (
                <TableRow>
                  <TableCell colSpan={6} sx={{ ...cellSx, textAlign: 'center', color: 'text.secondary' }}>
                    No episodes for {symbol}.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        )}
        {isFetching && (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 2 }}>
            <CircularProgress size={20} />
          </Box>
        )}
      </Box>

      <Divider />

      {/* Footer totals (buy vs sell footprint notional) */}
      <Box sx={{ p: 1.5, pt: 1 }}>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}>
          <Typography sx={{ fontSize: 13, color: 'text.secondary' }}>Buy footprint</Typography>
          <Typography sx={{ fontSize: 13, color: BUY, fontWeight: 700 }}>{fmtValue(totals.buy)}</Typography>
        </Box>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, mt: 0.3 }}>
          <Typography sx={{ fontSize: 13, color: 'text.secondary' }}>Sell footprint</Typography>
          <Typography sx={{ fontSize: 13, color: SELL, fontWeight: 700 }}>{fmtValue(totals.sell)}</Typography>
        </Box>
        <Typography sx={{ fontSize: 11, color: 'text.disabled', mt: 0.5 }}>
          {episodes.length} episodes
          {dataUpdatedAt ? ` · updated ${new Date(dataUpdatedAt).toLocaleTimeString('en-GB', { hour12: false })}` : ''}
        </Typography>
      </Box>
    </Box>
  );
}
