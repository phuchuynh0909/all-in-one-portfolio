import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Avatar,
  Box,
  Button,
  Collapse,
  Divider,
  Drawer,
  IconButton,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import SettingsIcon from '@mui/icons-material/Settings';
import SendIcon from '@mui/icons-material/Send';
import LightbulbOutlinedIcon from '@mui/icons-material/LightbulbOutlined';
import StopIcon from '@mui/icons-material/Stop';
import ThumbUpOutlinedIcon from '@mui/icons-material/ThumbUpOutlined';
import ThumbDownOutlinedIcon from '@mui/icons-material/ThumbDownOutlined';
import { MarkdownContent } from '../components/chat/MarkdownContent';
import {
  refreshAccessToken,
  startChatStream,
  type ChatStreamMessage,
} from '../lib/services/chat';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  isStreaming?: boolean;
}

const DOLPHIN_AVATAR = '🐬';

const ChatAgents = () => {
  const [accessToken, setAccessToken] = useState('');
  const [refreshToken, setRefreshToken] = useState('');
  const [masterAccount, setMasterAccount] = useState('');
  const [codeVerifier, setCodeVerifier] = useState('');
  const [deviceId, setDeviceId] = useState('');
  const [xChannel, setXChannel] = useState('S24');
  const [xClientDeviceId, setXClientDeviceId] = useState('');
  const [xClientRequestId, setXClientRequestId] = useState('');
  const [xMasterAccount, setXMasterAccount] = useState('');
  const [xVersion, setXVersion] = useState('v1.2.84');
  const [showAdvanced, setShowAdvanced] = useState(false);

  const [refreshLoading, setRefreshLoading] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [refreshResult, setRefreshResult] = useState<Record<string, unknown> | null>(null);

  const [inputValue, setInputValue] = useState('');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const streamControllerRef = useRef<AbortController | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);

  const [settingsPaste, setSettingsPaste] = useState('');

  const canRefresh = useMemo(() => accessToken.trim() && refreshToken.trim(), [accessToken, refreshToken]);

  const handleParseSettingsPaste = () => {
    const raw = settingsPaste.trim();
    if (!raw) return;
    try {
      const params = new URLSearchParams(raw.replace(/\n/g, '&'));
      const master = params.get('master_account');
      const refresh = params.get('refresh_token');
      const verifier = params.get('code_verifier');
      const device = params.get('device_id');
      if (master) setMasterAccount(master);
      if (refresh) setRefreshToken(refresh);
      if (verifier) setCodeVerifier(verifier);
      if (device) setDeviceId(device);
      setSettingsPaste('');
    } catch {
      // ignore parse errors
    }
  };

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  const handleRefreshToken = async () => {
    setRefreshLoading(true);
    setRefreshError(null);
    setRefreshResult(null);

    try {
      const payload = {
        access_token: accessToken.trim(),
        refresh_token: refreshToken.trim(),
        master_account: masterAccount.trim() || undefined,
        code_verifier: codeVerifier.trim() || undefined,
        device_id: deviceId.trim() || undefined,
        x_channel: xChannel.trim() || undefined,
        x_client_device_id: xClientDeviceId.trim() || undefined,
        x_client_request_id: xClientRequestId.trim() || undefined,
        x_master_account: xMasterAccount.trim() || undefined,
        x_version: xVersion.trim() || undefined,
      };

      const result = await refreshAccessToken(payload);
      setRefreshResult(result);

      if (typeof result.access_token === 'string') {
        setAccessToken(result.access_token);
      }
      if (typeof result.refresh_token === 'string') {
        setRefreshToken(result.refresh_token);
      }
    } catch (error) {
      setRefreshError(error instanceof Error ? error.message : 'Failed to refresh token');
    } finally {
      setRefreshLoading(false);
    }
  };

  const handleSend = () => {
    const query = inputValue.trim();
    if (!query || !accessToken.trim() || isStreaming) return;

    setInputValue('');
    setStreamError(null);

    const userMessage: ChatMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      content: query,
    };
    const assistantMessageId = `assistant-${Date.now()}`;
    const assistantMessage: ChatMessage = {
      id: assistantMessageId,
      role: 'assistant',
      content: '',
      isStreaming: true,
    };

    setMessages((prev) => [...prev, userMessage, assistantMessage]);
    setIsStreaming(true);

    const { controller } = startChatStream(
      {
        query,
        bearer_token: accessToken.trim(),
        refresh_token: refreshToken.trim() || undefined,
        master_account: masterAccount.trim() || undefined,
        code_verifier: codeVerifier.trim() || undefined,
        device_id: deviceId.trim() || undefined,
      },
      {
        onTokenRefreshed: (tokens) => {
          if (tokens.access_token) setAccessToken(tokens.access_token);
          if (tokens.refresh_token) setRefreshToken(tokens.refresh_token);
        },
        onMessage: (message: ChatStreamMessage) => {
          if (message.text) {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantMessageId
                  ? { ...m, content: m.content + message.text }
                  : m,
              ),
            );
          }
        },
        onError: (error) => {
          setStreamError(error instanceof Error ? error.message : 'Stream error');
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMessageId
                ? { ...m, content: m.content || 'Sorry, an error occurred.', isStreaming: false }
                : m,
            ),
          );
          setIsStreaming(false);
        },
        onComplete: () => {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMessageId ? { ...m, isStreaming: false } : m,
            ),
          );
          setIsStreaming(false);
          streamControllerRef.current = null;
        },
      },
    );

    streamControllerRef.current = controller;
  };

  const handleStopStream = () => {
    streamControllerRef.current?.abort();
    streamControllerRef.current = null;
    setIsStreaming(false);
    setMessages((prev) =>
      prev.map((m) => (m.isStreaming ? { ...m, isStreaming: false } : m)),
    );
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        bgcolor: 'background.default',
      }}
    >
      {/* Header */}
      <Box
        sx={{
          flexShrink: 0,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          px: 3,
          py: 2,
          borderBottom: 1,
          borderColor: 'divider',
        }}
      >
        <Stack direction="row" alignItems="center" spacing={2}>
          <Avatar
            sx={{
              bgcolor: 'primary.main',
              width: 40,
              height: 40,
              fontSize: '1.25rem',
            }}
          >
            {DOLPHIN_AVATAR}
          </Avatar>
          <Typography variant="h6" fontWeight={600}>
            Dolphin AI
          </Typography>
        </Stack>
        <IconButton onClick={() => setSettingsOpen(true)} color="inherit" size="large">
          <SettingsIcon />
        </IconButton>
      </Box>

      {/* Messages */}
      <Box
        ref={messagesContainerRef}
        sx={{
          flex: 1,
          overflowY: 'auto',
          px: 3,
          py: 2,
        }}
      >
        {messages.length === 0 && (
          <Box
            sx={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              height: '100%',
              color: 'text.secondary',
            }}
          >
            <Typography variant="body1" gutterBottom>
              Hãy hỏi bất cứ điều gì
            </Typography>
            <Typography variant="body2">
              Ví dụ: Phân tích báo cáo tài chính VCG
            </Typography>
          </Box>
        )}
        {messages.map((msg) => (
          <Box
            key={msg.id}
            sx={{
              display: 'flex',
              flexDirection: msg.role === 'user' ? 'row-reverse' : 'row',
              mb: 2,
              gap: 2,
            }}
          >
            {msg.role === 'assistant' && (
              <Avatar
                sx={{
                  bgcolor: 'primary.main',
                  width: 36,
                  height: 36,
                  fontSize: '1rem',
                  flexShrink: 0,
                }}
              >
                {DOLPHIN_AVATAR}
              </Avatar>
            )}
            <Box
              sx={{
                maxWidth: '75%',
                px: 2.5,
                py: 1.5,
                borderRadius: 2,
                boxShadow: 1,
                bgcolor: msg.role === 'user' ? 'primary.main' : 'background.paper',
                color: msg.role === 'user' ? 'primary.contrastText' : 'text.primary',
                border: msg.role === 'assistant' ? 1 : 0,
                borderColor: 'divider',
              }}
            >
              {msg.role === 'user' ? (
                <Typography sx={{ whiteSpace: 'pre-wrap' }}>{msg.content}</Typography>
              ) : (
                <Box>
                  {msg.content ? (
                    <MarkdownContent content={msg.content} />
                  ) : msg.isStreaming ? (
                    <Typography component="span" color="text.secondary">
                      Đang phân tích...
                    </Typography>
                  ) : null}
                </Box>
              )}
              {msg.role === 'assistant' && msg.content && !msg.isStreaming && (
                <Stack direction="row" spacing={0.5} sx={{ mt: 1.5 }}>
                  <IconButton size="small" sx={{ color: 'text.secondary' }}>
                    <ThumbUpOutlinedIcon fontSize="small" />
                  </IconButton>
                  <IconButton size="small" sx={{ color: 'text.secondary' }}>
                    <ThumbDownOutlinedIcon fontSize="small" />
                  </IconButton>
                </Stack>
              )}
            </Box>
          </Box>
        ))}
        <div ref={messagesEndRef} />
      </Box>

      {/* Error banner */}
      {streamError && (
        <Box sx={{ px: 3, py: 1 }}>
          <Alert severity="error" onClose={() => setStreamError(null)}>
            {streamError}
          </Alert>
        </Box>
      )}

      {/* Input area */}
      <Box
        sx={{
          flexShrink: 0,
          px: 3,
          py: 2,
          borderTop: 1,
          borderColor: 'divider',
          bgcolor: 'background.default',
        }}
      >
        <Stack direction="row" spacing={1.5} alignItems="flex-end">
          <IconButton size="medium" sx={{ color: 'text.secondary', mb: 0.5 }}>
            <LightbulbOutlinedIcon />
          </IconButton>
          <TextField
            fullWidth
            multiline
            maxRows={4}
            placeholder="Hãy hỏi bất cứ điều gì"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isStreaming || !accessToken.trim()}
            variant="outlined"
            sx={{
              '& .MuiOutlinedInput-root': {
                borderRadius: 3,
                bgcolor: 'background.paper',
              },
            }}
          />
          <IconButton
            color="primary"
            onClick={isStreaming ? handleStopStream : handleSend}
            disabled={!accessToken.trim() || (!isStreaming && !inputValue.trim())}
            sx={{
              bgcolor: 'primary.main',
              color: 'primary.contrastText',
              mb: 0.5,
              '&:hover': { bgcolor: 'primary.dark' },
              '&:disabled': { bgcolor: 'action.disabledBackground' },
            }}
          >
            {isStreaming ? (
              <StopIcon fontSize="small" />
            ) : (
              <SendIcon fontSize="small" />
            )}
          </IconButton>
        </Stack>
      </Box>

      {/* Settings Drawer */}
      <Drawer
        anchor="right"
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        PaperProps={{ sx: { width: { xs: '100%', sm: 420 } } }}
      >
        <Box sx={{ p: 3 }}>
          <Typography variant="h6" gutterBottom>
            Cài đặt
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Cấu hình token để kết nối với Dolphin AI
          </Typography>

          <Stack spacing={2}>
            <TextField
              label="Paste settings string"
              value={settingsPaste}
              onChange={(e) => setSettingsPaste(e.target.value)}
              multiline
              minRows={2}
              placeholder="master_account=AK0909&refresh_token=eyJ...&code_verifier=...&device_id=..."
              helperText="Paste URL-encoded form data, then click Parse & apply"
              fullWidth
              size="small"
            />
            <Button variant="outlined" onClick={handleParseSettingsPaste} disabled={!settingsPaste.trim()}>
              Parse & apply
            </Button>

            <Divider />

            <TextField
              label="Access Token"
              value={accessToken}
              onChange={(e) => setAccessToken(e.target.value)}
              multiline
              minRows={3}
              placeholder="Paste access token"
              fullWidth
              size="small"
            />
            <TextField
              label="Refresh Token"
              value={refreshToken}
              onChange={(e) => setRefreshToken(e.target.value)}
              multiline
              minRows={3}
              placeholder="Paste refresh token"
              fullWidth
              size="small"
            />

            <Button variant="text" onClick={() => setShowAdvanced((prev) => !prev)}>
              {showAdvanced ? 'Ẩn tùy chọn nâng cao' : 'Hiện tùy chọn nâng cao'}
            </Button>

            <Collapse in={showAdvanced}>
              <Stack spacing={2}>
                <Divider />
                <TextField label="Master Account" value={masterAccount} onChange={(e) => setMasterAccount(e.target.value)} fullWidth size="small" />
                <TextField label="Code Verifier" value={codeVerifier} onChange={(e) => setCodeVerifier(e.target.value)} fullWidth size="small" />
                <TextField label="Device ID" value={deviceId} onChange={(e) => setDeviceId(e.target.value)} fullWidth size="small" />
                <TextField label="X-Channel" value={xChannel} onChange={(e) => setXChannel(e.target.value)} fullWidth size="small" />
                <TextField label="X-Client-Device-Id" value={xClientDeviceId} onChange={(e) => setXClientDeviceId(e.target.value)} fullWidth size="small" />
                <TextField label="X-Client-Request-Id" value={xClientRequestId} onChange={(e) => setXClientRequestId(e.target.value)} fullWidth size="small" />
                <TextField label="X-Master-Account" value={xMasterAccount} onChange={(e) => setXMasterAccount(e.target.value)} fullWidth size="small" />
                <TextField label="X-Version" value={xVersion} onChange={(e) => setXVersion(e.target.value)} fullWidth size="small" />
              </Stack>
            </Collapse>

            {refreshError && <Alert severity="error">{refreshError}</Alert>}
            {refreshResult && <Alert severity="success">Token đã được làm mới</Alert>}

            <Button
              variant="contained"
              disabled={!canRefresh || refreshLoading}
              onClick={handleRefreshToken}
              fullWidth
            >
              {refreshLoading ? 'Đang làm mới...' : 'Làm mới Token'}
            </Button>
          </Stack>
        </Box>
      </Drawer>
    </Box>
  );
};

export default ChatAgents;
