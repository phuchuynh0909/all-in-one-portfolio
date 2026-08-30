/**
 * Chart watchlist panel.
 *
 * A stand-in for the charting library's Watch List widget, which ships only
 * with the Trading Terminal package (see `lib/tv/watchlist.ts` for how that is
 * detected). The panel holds no list state of its own: everything goes through
 * the `IWatchListApi` it is handed, so the same component would be redundant —
 * not broken — the day the native widget takes over.
 *
 * That buys the widget's own semantics: several named lists, `###`-prefixed
 * section dividers, and change subscriptions rather than ad-hoc re-reads.
 *
 * Rows are quoted from `POST /quote/batch`: live matched trades where the
 * provider has them, and the app's own last end-of-day bar for indices and
 * symbols that have not traded yet. Polling follows the VN session, so a closed
 * market costs one request on mount and nothing after.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  IconButton,
  InputAdornment,
  List,
  ListItemButton,
  Menu,
  MenuItem,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import {
  Add,
  ArrowDropDown,
  Check,
  Close,
  DeleteOutline,
  DriveFileRenameOutline,
  PlaylistAdd,
} from '@mui/icons-material';

import type { IWatchListApi, WatchListSymbolListMap } from '../../lib/tv';
import { isSectionDivider, listSymbols, sectionTitle, SECTION_PREFIX } from '../../lib/tv/watchlist';
import { fetchQuotes, isVnMarketSession, type LatestQuote } from '../../lib/services/quote';

/** How often quotes refresh while the market is open. */
const POLL_MS = 5_000;

const UP = 'var(--color-long)';
const DOWN = 'var(--color-short)';
const FLAT = 'var(--color-flat)';

/** Prices are in thousands of VND; indices are whole numbers of points. */
function formatPrice(quote: LatestQuote): string {
  return quote.price.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function formatPct(pct: number): string {
  return `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%`;
}

function colorFor(pct: number | null | undefined): string {
  if (pct == null || pct === 0) return FLAT;
  return pct > 0 ? UP : DOWN;
}

export interface WatchlistProps {
  /** List state and change events — the library's own API, or the app's. */
  api: IWatchListApi;
  /** Symbol currently shown on the chart (highlighted in the list). */
  activeSymbol: string;
  /** Called when a row is clicked. */
  onSelect: (symbol: string) => void;
}

export default function Watchlist({ api, activeSymbol, onSelect }: WatchlistProps) {
  const [lists, setLists] = useState<WatchListSymbolListMap>({});
  const [activeListId, setActiveListId] = useState<string | null>(null);
  /** Raw list contents — symbols *and* `###` section dividers, in order. */
  const [items, setItems] = useState<string[]>([]);

  const [quotes, setQuotes] = useState<Record<string, LatestQuote>>({});
  const [unavailable, setUnavailable] = useState<Set<string>>(new Set());
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [listMenuAnchor, setListMenuAnchor] = useState<HTMLElement | null>(null);
  const [nameDialog, setNameDialog] = useState<'create' | 'rename' | null>(null);
  const [nameDraft, setNameDraft] = useState('');

  const readFromApi = useCallback(() => {
    setLists(api.getAllLists() ?? {});
    const id = api.getActiveListId();
    setActiveListId(id);
    setItems(api.getList(id ?? undefined) ?? []);
  }, [api]);

  // One subscription per event; `unsubscribeAll(owner)` drops them together.
  useEffect(() => {
    const owner = {};
    readFromApi();
    const events = [
      api.onListChanged(),
      api.onActiveListChanged(),
      api.onListAdded(),
      api.onListRemoved(),
      api.onListRenamed(),
    ];
    // Every event only means "re-read": the API is the single source of truth.
    events.forEach((event) => event.subscribe(owner, readFromApi));
    return () => events.forEach((event) => event.unsubscribeAll(owner));
  }, [api, readFromApi]);

  const symbols = useMemo(() => listSymbols(items), [items]);
  const symbolsKey = symbols.join(',');

  // The poll reads the current symbols without re-subscribing on every edit.
  const symbolsRef = useRef(symbols);
  symbolsRef.current = symbols;

  const refresh = useCallback(async () => {
    const list = symbolsRef.current;
    if (list.length === 0) {
      setQuotes({});
      setUnavailable(new Set());
      return;
    }
    try {
      const batch = await fetchQuotes(list);
      setQuotes(Object.fromEntries(batch.quotes.map((q) => [q.symbol, q])));
      setUnavailable(new Set(batch.unavailable));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load quotes');
    }
  }, []);

  // Quote the list as soon as it changes (added symbol, switched list) rather
  // than waiting for the next tick.
  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbolsKey]);

  useEffect(() => {
    // Keep refreshing only while the market is open; the effect above already
    // primed the list.
    const timer = window.setInterval(() => {
      if (isVnMarketSession()) void refresh();
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const orderedLists = useMemo(
    () => Object.values(lists).sort((a, b) => a.title.localeCompare(b.title)),
    [lists],
  );
  const activeList = activeListId ? lists[activeListId] : undefined;

  /** Writes the whole list back — the only mutation the widget's API offers. */
  const writeItems = (next: string[]) => {
    if (activeListId) api.updateList(activeListId, next);
  };

  const addItem = () => {
    const entry = draft.trim();
    setDraft('');
    setAdding(false);
    if (!entry) return;
    // `###Name` adds a section divider, matching the widget's own convention.
    writeItems([...items, isSectionDivider(entry) ? entry : entry.toUpperCase()]);
  };

  /** Removal is positional: section dividers may legitimately repeat. */
  const removeAt = (index: number) => writeItems(items.filter((_, i) => i !== index));

  const submitName = () => {
    const name = nameDraft.trim();
    if (name) {
      if (nameDialog === 'create') api.createList(name, []);
      else if (activeListId) api.renameList(activeListId, name);
    }
    setNameDialog(null);
    setNameDraft('');
  };

  const active = activeSymbol.trim().toUpperCase();
  const onlyOneList = orderedLists.length <= 1;

  return (
    // maxHeight rather than height: the panel sizes to its content and only
    // caps at the parent, so a short list does not stretch and leave dead space
    // above whatever is stacked beneath it. The inner List still scrolls once
    // the cap is reached.
    <Box sx={{ display: 'flex', flexDirection: 'column', maxHeight: '100%', minHeight: 0, width: '100%' }}>
      <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ px: 0.5, pb: 0.5 }}>
        <Button
          size="small"
          endIcon={<ArrowDropDown />}
          onClick={(e) => setListMenuAnchor(e.currentTarget)}
          sx={{
            color: 'text.primary',
            fontWeight: 600,
            fontSize: 13,
            letterSpacing: 0.3,
            textTransform: 'none',
            minWidth: 0,
            px: 0.75,
          }}
        >
          <Box component="span" sx={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {activeList?.title ?? 'Watchlist'}
          </Box>
        </Button>
        <Tooltip title={adding ? 'Cancel' : 'Add symbol'}>
          <IconButton size="small" onClick={() => { setAdding((p) => !p); setDraft(''); }} sx={{ color: 'text.secondary' }}>
            {adding ? <Close fontSize="small" /> : <Add fontSize="small" />}
          </IconButton>
        </Tooltip>
      </Stack>

      <Menu
        anchorEl={listMenuAnchor}
        open={!!listMenuAnchor}
        onClose={() => setListMenuAnchor(null)}
        MenuListProps={{ dense: true }}
      >
        {orderedLists.map((list) => (
          <MenuItem
            key={list.id}
            selected={list.id === activeListId}
            onClick={() => { api.setActiveList(list.id); setListMenuAnchor(null); }}
          >
            <Box sx={{ width: 22, display: 'flex', alignItems: 'center' }}>
              {list.id === activeListId ? <Check sx={{ fontSize: 15 }} /> : null}
            </Box>
            {list.title}
            <Typography component="span" sx={{ ml: 1.5, fontSize: 11, color: FLAT }}>
              {listSymbols(list.symbols).length}
            </Typography>
          </MenuItem>
        ))}
        <Divider />
        <MenuItem
          onClick={() => { setListMenuAnchor(null); setNameDraft(''); setNameDialog('create'); }}
        >
          <PlaylistAdd sx={{ fontSize: 16, mr: 1 }} /> New list...
        </MenuItem>
        <MenuItem
          disabled={!activeList}
          onClick={() => {
            setListMenuAnchor(null);
            setNameDraft(activeList?.title ?? '');
            setNameDialog('rename');
          }}
        >
          <DriveFileRenameOutline sx={{ fontSize: 16, mr: 1 }} /> Rename list...
        </MenuItem>
        <MenuItem
          disabled={onlyOneList || !activeListId}
          onClick={() => {
            setListMenuAnchor(null);
            if (activeListId) api.deleteList(activeListId);
          }}
        >
          <DeleteOutline sx={{ fontSize: 16, mr: 1 }} /> Delete list
        </MenuItem>
      </Menu>

      {adding && (
        <Box sx={{ px: 0.5, pb: 1 }}>
          <TextField
            autoFocus
            fullWidth
            size="small"
            placeholder="Ticker, or ###Section"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') addItem();
              if (e.key === 'Escape') { setAdding(false); setDraft(''); }
            }}
            InputProps={{
              endAdornment: (
                <InputAdornment position="end">
                  <IconButton size="small" onClick={addItem} sx={{ color: 'text.secondary' }}>
                    <Add fontSize="small" />
                  </IconButton>
                </InputAdornment>
              ),
              sx: { color: 'text.primary', fontSize: 13 },
            }}
            sx={{
              '& .MuiOutlinedInput-notchedOutline': { borderColor: 'line.default' },
            }}
          />
        </Box>
      )}

      {error && (
        <Typography variant="caption" sx={{ px: 1, pb: 0.5, color: DOWN }}>
          {error}
        </Typography>
      )}

      <List dense disablePadding sx={{ overflowY: 'auto', flex: 1, minHeight: 0 }}>
        {items.length === 0 && (
          <Typography variant="caption" sx={{ px: 1, color: FLAT }}>
            No symbols yet — add one with the + button.
          </Typography>
        )}
        {items.map((item, index) => {
          if (isSectionDivider(item)) {
            return (
              <Stack
                key={`${item}-${index}`}
                direction="row"
                alignItems="center"
                sx={{
                  px: 1,
                  pt: 1,
                  pb: 0.25,
                  '& .wl-remove': { opacity: 0 },
                  '&:hover .wl-remove': { opacity: 1 },
                }}
              >
                <Typography
                  noWrap
                  sx={{ flex: 1, fontSize: 10, fontWeight: 700, letterSpacing: 0.8, color: FLAT, textTransform: 'uppercase' }}
                >
                  {sectionTitle(item) || SECTION_PREFIX}
                </Typography>
                <Tooltip title="Remove section">
                  <IconButton
                    className="wl-remove"
                    size="small"
                    onClick={() => removeAt(index)}
                    sx={{ color: FLAT, transition: 'opacity 120ms' }}
                  >
                    <DeleteOutline sx={{ fontSize: 14 }} />
                  </IconButton>
                </Tooltip>
              </Stack>
            );
          }

          const quote = quotes[item];
          const isActive = item === active;
          const pct = quote?.change_pct ?? null;
          return (
            <ListItemButton
              key={`${item}-${index}`}
              selected={isActive}
              onClick={() => onSelect(item)}
              sx={{
                py: 0.5,
                px: 1,
                borderLeft: '2px solid',
                borderLeftColor: isActive ? 'primary.main' : 'transparent',
                '&.Mui-selected': { bgcolor: 'action.selected' },
                '&.Mui-selected:hover': { bgcolor: 'action.selected' },
                '& .wl-remove': { opacity: 0 },
                '&:hover .wl-remove': { opacity: 1 },
              }}
            >
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Stack direction="row" alignItems="center" spacing={0.5}>
                  <Typography
                    noWrap
                    sx={{ fontSize: 13, fontWeight: 600, color: isActive ? 'primary.main' : 'text.primary' }}
                  >
                    {item}
                  </Typography>
                  {quote?.source === 'eod' && (
                    <Tooltip title="End-of-day close — no live trade for this symbol">
                      <Typography sx={{ fontSize: 9, color: FLAT, border: '1px solid', borderColor: FLAT, borderRadius: 0.5, px: 0.3, lineHeight: 1.4 }}>
                        EOD
                      </Typography>
                    </Tooltip>
                  )}
                </Stack>
              </Box>

              <Stack alignItems="flex-end" sx={{ minWidth: 84 }}>
                <Typography sx={{ fontSize: 13, color: quote ? 'text.primary' : FLAT, fontVariantNumeric: 'tabular-nums' }}>
                  {quote ? formatPrice(quote) : unavailable.has(item) ? 'n/a' : '—'}
                </Typography>
                {pct != null && (
                  <Typography sx={{ fontSize: 11, color: colorFor(pct), fontVariantNumeric: 'tabular-nums' }}>
                    {formatPct(pct)}
                  </Typography>
                )}
              </Stack>

              <Tooltip title="Remove">
                <IconButton
                  className="wl-remove"
                  size="small"
                  onClick={(e) => { e.stopPropagation(); removeAt(index); }}
                  sx={{ ml: 0.5, color: FLAT, transition: 'opacity 120ms' }}
                >
                  <DeleteOutline sx={{ fontSize: 15 }} />
                </IconButton>
              </Tooltip>
            </ListItemButton>
          );
        })}
      </List>

      <Dialog open={!!nameDialog} onClose={() => setNameDialog(null)} maxWidth="xs" fullWidth>
        <DialogTitle sx={{ fontSize: 16 }}>
          {nameDialog === 'rename' ? 'Rename list' : 'New list'}
        </DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            fullWidth
            size="small"
            placeholder="List name"
            value={nameDraft}
            onChange={(e) => setNameDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') submitName(); }}
            sx={{ mt: 1 }}
          />
        </DialogContent>
        <DialogActions>
          <Button size="small" onClick={() => setNameDialog(null)}>Cancel</Button>
          <Button size="small" onClick={submitName} disabled={!nameDraft.trim()}>
            {nameDialog === 'rename' ? 'Rename' : 'Create'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
