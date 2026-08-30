/**
 * Chat notes for the active symbol, rendered inside the chart's right-hand
 * column. Fetches its own data and brings its own header, matching
 * Watchlist and AnomalyPanel so the rail can swap between the three.
 */
import { useCallback, useEffect, useState } from 'react';
import { Box, CircularProgress, IconButton, Stack, Tooltip, Typography } from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import { getChatNotes, type ChatNoteItem } from '../../lib/services/chat';
import { MarkdownContent } from '../chat/MarkdownContent';
import { EmptyState, ErrorState } from '../ui';

export default function NotesPanel({ symbol }: { symbol: string }) {
  const [notes, setNotes] = useState<ChatNoteItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const sym = symbol.trim().toUpperCase();
    if (!sym) {
      setNotes([]);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const response = await getChatNotes(sym);
      setNotes(response.notes);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load notes');
    } finally {
      setLoading(false);
    }
  }, [symbol]);

  // Refetch whenever the chart's symbol changes.
  useEffect(() => {
    void load();
  }, [load]);

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', minHeight: 0, minWidth: 0, width: '100%' }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        sx={{ px: 0.75, pb: 0.5, flexShrink: 0, minWidth: 0 }}
      >
        <Typography sx={{ fontSize: 11.5, fontWeight: 700, letterSpacing: 0.3 }}>NOTES</Typography>
        <Typography sx={{ fontSize: 11.5, color: 'primary.light', fontWeight: 700 }}>
          {symbol}
        </Typography>
        {loading && <CircularProgress size={10} />}
        <Box sx={{ flex: 1 }} />
        {!loading && notes.length > 0 && (
          <Typography sx={{ fontSize: 10, color: 'text.disabled', whiteSpace: 'nowrap' }}>
            {notes.length} {notes.length === 1 ? 'note' : 'notes'}
          </Typography>
        )}
        <Tooltip title="Refresh">
          <span>
            <IconButton size="small" onClick={() => void load()} disabled={loading} sx={{ p: 0.25 }}>
              <RefreshIcon sx={{ fontSize: 14 }} />
            </IconButton>
          </span>
        </Tooltip>
      </Stack>

      <Box sx={{ flex: 1, minHeight: 0, overflowY: 'auto', px: 0.75, pb: 0.75 }}>
        {error ? (
          <ErrorState error={error} title="Could not load notes" onRetry={() => void load()} />
        ) : !loading && notes.length === 0 ? (
          <EmptyState
            compact
            title="No notes"
            description={`Nothing saved for ${symbol} yet.`}
          />
        ) : (
          <Stack spacing={1}>
            {notes.map((note, idx) => (
              <Box
                key={`${note.created_at}-${idx}`}
                sx={{
                  p: 1,
                  border: 1,
                  borderColor: 'line.subtle',
                  borderRadius: 1,
                  bgcolor: 'surface.inset',
                }}
              >
                <Typography variant="mono" sx={{ fontSize: 10, color: 'primary.main' }}>
                  {note.created_at ? new Date(note.created_at).toLocaleString() : 'N/A'}
                </Typography>
                <Box sx={{ mt: 0.5, fontSize: '0.8125rem', lineHeight: 1.6, wordBreak: 'break-word' }}>
                  <MarkdownContent content={note.message} />
                </Box>
              </Box>
            ))}
          </Stack>
        )}
      </Box>
    </Box>
  );
}
