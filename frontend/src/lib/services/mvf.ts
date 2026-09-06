import { API_BASE_URL } from '../api';

export interface MvfRequest {
  tickers: string[];
  benchmark?: string;
  seq_len?: number;
  horizon?: number;
  epochs?: number;
  lr?: number;
  batch_size?: number;
  force_retrain?: boolean;
  max_weight?: number;
  cov_lookback?: number;
  cov_shrink?: boolean;
  risk_free_rate?: number;
  capital?: number;
  years?: number;
}

export interface MvfHolding {
  ticker: string;
  weight: number;
  pred_ann_return: number;
  ann_vol: number;
  last_price: number;
  shares: number;
  target_value: number;
  alloc_value: number;
}

export interface MvfAllocationSnapshot {
  as_of: string;
  predicted_return: number;
  predicted_volatility: number;
  predicted_sharpe: number;
  weight_sum: number;
  holdings: MvfHolding[];
}

export interface MvfResult {
  as_of: string;
  train_cutoff: string;
  bars: number;
  universe: string[];
  dropped: string[];
  excluded: string[];
  horizon: number;
  max_weight: number;
  predicted_return: number;
  predicted_volatility: number;
  predicted_sharpe: number;
  weight_sum: number;
  capital: number;
  deployed_value: number;
  cash_residual: number;
  holdings: MvfHolding[];
  allocation_history: MvfAllocationSnapshot[];
}

export interface MvfStarted {
  tickers: string[];
  device: string;
  horizon: number;
}

export interface MvfLoaded {
  universe: string[];
  dropped: string[];
  bars: number;
  start: string;
  end: string;
}

/** One per-asset model finished: either freshly `trained` or loaded from cache. */
export interface MvfAsset {
  symbol: string;
  index: number;
  total: number;
  source: 'trained' | 'cached';
}

export interface MvfHandlers {
  onStarted?: (data: MvfStarted) => void;
  onLoaded?: (data: MvfLoaded) => void;
  onAsset?: (data: MvfAsset) => void;
  onForecasting?: () => void;
  onOptimizing?: () => void;
  onResult?: (result: MvfResult) => void;
  onError?: (error: unknown) => void;
  onComplete?: () => void;
}

/**
 * Start an MVF run. Training one LSTM per asset takes minutes on a cold cache,
 * so the backend streams SSE progress; this parses the `event:`/`data:` blocks
 * and dispatches to the handlers. Returns an AbortController so the caller can
 * cancel a run in flight.
 */
export const startMvfRun = (
  payload: MvfRequest,
  handlers: MvfHandlers,
): { controller: AbortController; promise: Promise<void> } => {
  const controller = new AbortController();

  const promise = (async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/portfolio/mvf/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
        },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });

      if (!response.ok) {
        throw new Error(`MVF stream failed: ${response.status}`);
      }
      if (!response.body) {
        throw new Error('MVF stream has no body');
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const blocks = buffer.split('\n\n');
        buffer = blocks.pop() ?? '';

        for (const block of blocks) {
          let eventType = 'message';
          const dataLines: string[] = [];
          for (const line of block.split('\n')) {
            if (line.startsWith('event:')) {
              eventType = line.slice('event:'.length).trim();
            } else if (line.startsWith('data:')) {
              dataLines.push(line.slice('data:'.length).trim());
            }
          }
          const dataText = dataLines.join('\n');
          if (!dataText) continue;

          let data: Record<string, unknown>;
          try {
            data = JSON.parse(dataText);
          } catch {
            continue;
          }

          switch (eventType) {
            case 'started':
              handlers.onStarted?.(data as unknown as MvfStarted);
              break;
            case 'loaded':
              handlers.onLoaded?.(data as unknown as MvfLoaded);
              break;
            case 'asset':
              handlers.onAsset?.(data as unknown as MvfAsset);
              break;
            case 'forecasting':
              handlers.onForecasting?.();
              break;
            case 'optimizing':
              handlers.onOptimizing?.();
              break;
            case 'result':
              handlers.onResult?.(data as unknown as MvfResult);
              break;
            case 'error':
              handlers.onError?.(
                new Error(String((data as { error?: string }).error ?? 'Unknown error')),
              );
              break;
            default:
              break;
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
