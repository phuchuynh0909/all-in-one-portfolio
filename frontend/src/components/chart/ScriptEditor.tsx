import { useEffect, useRef, useState } from 'react';
import { Alert, Box, Button, Chip, IconButton, MenuItem, Stack, TextField, Tooltip, Typography } from '@mui/material';
import { Close, Code, DeleteOutline, PlayArrow, SaveOutlined } from '@mui/icons-material';
import { SCRIPT_TEMPLATE, type UserScript } from '../../lib/tv/userScripts';
import { validateUserScript } from '../../lib/tv/scriptCompiler';

interface Props {
  scripts: UserScript[];
  ready: boolean;
  error: string;
  onSave: (id: string, source: string, add: boolean) => Promise<void>;
  onRemove: (id: string, deleteScript: boolean) => Promise<void>;
  onClose: () => void;
}

function initialDraft(scripts: UserScript[]): { id: string; source: string } {
  const first = scripts[0];
  return first ? { id: first.id, source: first.source } : { id: `user_${crypto.randomUUID()}`, source: SCRIPT_TEMPLATE };
}

export default function ScriptEditor({ scripts, ready, error, onSave, onRemove, onClose }: Props) {
  const [draft, setDraft] = useState(() => initialDraft(scripts));
  const [message, setMessage] = useState('');
  const [failure, setFailure] = useState('');
  const [busy, setBusy] = useState(false);
  const gutter = useRef<HTMLDivElement>(null);
  const script = scripts.find((item) => item.id === draft.id);
  const dirty = draft.source !== script?.source;

  useEffect(() => {
    if (ready) setMessage((previous) => previous === 'Script saved. Applying indicator to chart…' ? 'Indicator applied to chart.' : previous);
  }, [ready]);

  const changeSource = (source: string) => {
    setDraft({ ...draft, source });
    setMessage('');
    setFailure('');
  };

  const selectScript = (id: string) => {
    if (dirty && !window.confirm('Discard the changes in this editor?')) return;
    const selected = scripts.find((item) => item.id === id);
    setDraft(selected ? { id, source: selected.source } : { id: `user_${crypto.randomUUID()}`, source: SCRIPT_TEMPLATE });
    setMessage('');
    setFailure('');
  };

  const run = async (action: 'check' | 'save' | 'add' | 'remove' | 'delete') => {
    setFailure('');
    setMessage('');
    setBusy(true);
    try {
      if (action === 'check') {
        const result = validateUserScript(draft.source, draft.id);
        setMessage(`Compiled successfully · ${result.overlay ? 'Overlays price' : 'Separate pane'} · ${result.program.inputs.length} inputs`);
      } else if (action === 'save' || action === 'add') {
        await onSave(draft.id, draft.source, action === 'add');
        setMessage(action === 'save' ? 'Saved to your server library.' : 'Script saved. Applying indicator to chart…');
      } else {
        if (action === 'delete' && !window.confirm(`Delete “${script?.title}” from your saved scripts?`)) return;
        await onRemove(draft.id, action === 'delete');
        setMessage(action === 'delete' ? 'Script deleted. The source is still in your editor.' : 'Indicator removed from chart.');
      }
    } catch (cause) {
      setFailure(cause instanceof Error ? cause.message : 'Could not apply script.');
    } finally { setBusy(false); }
  };

  return (
    <Box component="section" aria-label="OpenScript editor" sx={{ height: { xs: 360, md: 330 }, flexShrink: 0, display: 'flex', flexDirection: 'column', borderTop: 1, borderColor: 'divider', bgcolor: 'background.paper' }}>
      <Stack direction="row" alignItems="center" gap={1} sx={{ px: 1.5, py: 1, flexWrap: 'wrap' }}>
        <Code fontSize="small" color="primary" />
        <Typography variant="subtitle2">OpenScript Editor</Typography>
        <TextField select size="small" value={script?.id ?? ''} onChange={(event) => selectScript(event.target.value)} disabled={busy || !ready} SelectProps={{ displayEmpty: true }} inputProps={{ 'aria-label': 'Saved scripts' }} sx={{ minWidth: 180, maxWidth: 260 }}>
          <MenuItem value="">New script</MenuItem>
          {scripts.map((item) => <MenuItem key={item.id} value={item.id}>{item.title}</MenuItem>)}
        </TextField>
        <Button size="small" onClick={() => selectScript('')} disabled={busy || !ready}>New</Button>
        {dirty && <Chip size="small" label="Unsaved" variant="outlined" />}
        {script?.visible && <Chip size="small" color="primary" label="On chart" />}
        {script?.visible && script.appliedSource !== draft.source && <Typography variant="caption" color="text.secondary">Changes not applied</Typography>}
        <Box sx={{ flex: 1 }} />
        <Button size="small" onClick={() => void run('check')} disabled={busy}>Check</Button>
        <Button size="small" startIcon={<SaveOutlined />} onClick={() => void run('save')} disabled={busy || !ready}>Save</Button>
        <Button size="small" variant="contained" startIcon={<PlayArrow />} onClick={() => void run('add')} disabled={busy || !ready}>{!ready ? 'Loading chart…' : script?.visible ? 'Update on chart' : 'Add to chart'}</Button>
        {script?.visible && <Button size="small" onClick={() => void run('remove')} disabled={busy || !ready}>Remove</Button>}
        {script && <Tooltip title="Delete saved script"><span><IconButton aria-label="Delete saved script" size="small" onClick={() => void run('delete')} disabled={busy || !ready}><DeleteOutline fontSize="small" /></IconButton></span></Tooltip>}
        <IconButton aria-label="Close script editor" size="small" onClick={onClose}><Close fontSize="small" /></IconButton>
      </Stack>
      <Box sx={{ flex: 1, minHeight: 80, display: 'flex', borderTop: 1, borderColor: 'divider', bgcolor: 'var(--color-bg-inset)' }}>
        <Box ref={gutter} aria-hidden="true" sx={{ overflow: 'hidden', flexShrink: 0, py: 1.25, px: 1.5, textAlign: 'right', color: 'text.disabled', fontFamily: 'monospace', fontSize: 13, lineHeight: '21px', whiteSpace: 'pre', userSelect: 'none' }}>
          {draft.source.split('\n').map((_, index) => index + 1).join('\n')}
        </Box>
        <Box component="textarea" aria-label="OpenScript source" spellCheck={false} value={draft.source}
          onChange={(event) => changeSource(event.target.value)}
          onScroll={(event) => { if (gutter.current) gutter.current.scrollTop = event.currentTarget.scrollTop; }}
          onKeyDown={(event) => {
            if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); if (!busy && ready) void run('add'); }
            if (event.key === 'Tab') {
              event.preventDefault();
              const target = event.currentTarget;
              const start = target.selectionStart;
              changeSource(`${draft.source.slice(0, start)}    ${draft.source.slice(target.selectionEnd)}`);
              requestAnimationFrame(() => { target.selectionStart = target.selectionEnd = start + 4; });
            }
          }}
          sx={{ flex: 1, minWidth: 0, resize: 'none', border: 0, outline: 0, p: 1.25, pl: 0, bgcolor: 'transparent', color: 'text.primary', fontFamily: 'monospace', fontSize: 13, lineHeight: '21px', whiteSpace: 'pre', overflowWrap: 'normal' }} />
      </Box>
      {(failure || error || message) && <Alert severity={failure || error ? 'error' : 'success'} role="status" sx={{ py: 0, maxHeight: 100, overflow: 'auto', flexShrink: 0, '& .MuiAlert-message': { whiteSpace: 'pre-wrap', fontFamily: 'monospace', fontSize: 12 } }}>{failure || error || message}</Alert>}
      <Typography variant="caption" color="text.secondary" sx={{ px: 1.5, py: 0.5 }}>OpenScript · Ctrl / ⌘ + Enter to apply · overlay = true draws on price; otherwise creates a pane · Saved to your account on the server</Typography>
    </Box>
  );
}
