import { API_BASE_URL, apiPost } from '../api';

export interface RefreshTokenRequest {
  access_token: string;
  refresh_token: string;
  master_account?: string;
  code_verifier?: string;
  device_id?: string;
  x_channel?: string;
  x_client_device_id?: string;
  x_client_request_id?: string;
  x_master_account?: string;
  x_version?: string;
}

export type RefreshTokenResponse = Record<string, unknown> & {
  access_token?: string;
  refresh_token?: string;
  expires_in?: number;
  refresh_expires_in?: number;
};

export interface ChatStreamRequest {
  query: string;
  bearer_token: string;
  refresh_token?: string;
  master_account?: string;
  code_verifier?: string;
  device_id?: string;
}

export interface ChatStreamMessage {
  text: string;
  eventType?: string;
  chatId?: string;
}

export interface ChatStreamHandlers {
  onOpen?: () => void;
  onMessage: (message: ChatStreamMessage) => void;
  onTokenRefreshed?: (tokens: { access_token: string; refresh_token?: string }) => void;
  onError?: (error: unknown) => void;
  onComplete?: () => void;
}

export interface SaveChatNoteRequest {
  symbol: string;
  message: string;
  chat_id?: string;
}

export interface SaveChatNoteResponse {
  status: string;
}

export interface ChatNoteItem {
  symbol: string;
  message: string;
  chat_id?: string | null;
  created_at: string;
}

export interface ChatNotesResponse {
  notes: ChatNoteItem[];
}

export const refreshAccessToken = async (
  payload: RefreshTokenRequest,
): Promise<RefreshTokenResponse> => apiPost('/broker/refresh-token', payload);

export const saveChatNote = async (
  payload: SaveChatNoteRequest,
): Promise<SaveChatNoteResponse> => apiPost('/chat/notes', payload);

export const getChatNotes = async (symbol: string): Promise<ChatNotesResponse> => {
  const response = await fetch(`${API_BASE_URL}/chat/notes?symbol=${encodeURIComponent(symbol)}`);
  if (!response.ok) {
    throw new Error(`GET /chat/notes failed: ${response.status}`);
  }
  return (await response.json()) as ChatNotesResponse;
};

export const startChatStream = (
  payload: ChatStreamRequest,
  handlers: ChatStreamHandlers,
): { controller: AbortController; promise: Promise<void> } => {
  const controller = new AbortController();

  const promise = (async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/chat/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
        },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });

      if (!response.ok) {
        throw new Error(`Chat stream failed: ${response.status}`);
      }

      if (!response.body) {
        throw new Error('Chat stream has no body');
      }

      handlers.onOpen?.();

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split('\n\n');
        buffer = parts.pop() ?? '';

        for (const part of parts) {
          const lines = part.split('\n');
          for (const line of lines) {
            if (!line.startsWith('data:')) continue;
            const payloadText = line.replace(/^data:\s?/, '').trim();
            if (!payloadText) continue;
            try {
              const data = JSON.parse(payloadText) as ChatStreamMessage & {
                error?: string;
                access_token?: string;
                refresh_token?: string;
              };
              if ('error' in data && typeof data.error === 'string') {
                handlers.onError?.(new Error(data.error));
                continue;
              }
              if ('access_token' in data && typeof data.access_token === 'string') {
                handlers.onTokenRefreshed?.({
                  access_token: data.access_token,
                  refresh_token: data.refresh_token,
                });
                continue;
              }
              handlers.onMessage(data);
            } catch (error) {
              handlers.onError?.(error);
            }
          }
        }
      }

      handlers.onComplete?.();
    } catch (error) {
      if ((error as { name?: string })?.name !== 'AbortError') {
        handlers.onError?.(error);
      }
    }
  })();

  return { controller, promise };
};
