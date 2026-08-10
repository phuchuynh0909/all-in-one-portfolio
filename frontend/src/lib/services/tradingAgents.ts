import { API_BASE_URL, apiGet } from '../api';

/**
 * Every model field takes a bare model name (served by the backend's default
 * provider) or a `provider:model` spec — so roles can run on different
 * providers, e.g. deep on `openai:…` and quick on `deepseek:…`.
 */
export interface AnalyzeRequest {
  symbol: string;
  trade_date?: string;
  analysts?: string[];
  /** Default analyst model (also researchers/trader/risk). Omit for the server default. */
  quick_think_llm?: string;
  /** Research- + portfolio-manager model. Omit for the server default. */
  deep_think_llm?: string;
  /** Per-analyst model, e.g. { market: 'ollama:qwen3:latest' }. Wins over quick_think_llm. */
  analyst_models?: Record<string, string>;
}

/** What a role resolved to. */
export interface TALlmRole {
  provider: string;
  model: string;
}

export interface TAProviderModels {
  quick: string[];
  deep: string[];
  key_env: string | null;
  /** API key present (or none needed) — a run on this provider can start. */
  ready: boolean;
}

/** Model choices per provider (GET /trading-agents/models). */
export interface TAModelOptions {
  /** Provider used for bare model names. */
  provider: string;
  providers: Record<string, TAProviderModels>;
  /** The default provider's catalog. Suggestions, not a whitelist. */
  options: { quick: string[]; deep: string[] };
  defaults: {
    deep_think_llm: string;
    quick_think_llm: string;
    analyst_llms: Record<string, string>;
    llm_roles: Record<string, TALlmRole>;
  };
  analyst_keys: string[];
  default_analysts: string[];
}

export interface TAReport {
  section: string;
  content: string;
}

export interface TADecision {
  signal: string;
  full: string;
}

export interface TAHealth {
  ollama_reachable: boolean;
  message: string;
  /** Provider for bare model names; a run may use several. */
  provider: string;
  providers?: string[];
  deep_think_llm: string;
  quick_think_llm: string;
  /** Analysts pinned to their own model by env; the rest run on quick_think_llm. */
  analyst_llms?: Record<string, string>;
  llm_roles?: Record<string, TALlmRole>;
}

/** `started` event payload — echoes the models actually resolved for the run. */
export interface TAStarted {
  symbol: string;
  date: string;
  analysts: string[];
  provider?: string;
  deep_think_llm?: string;
  quick_think_llm?: string;
  analyst_llms?: Record<string, string>;
  /** role ('deep' | 'quick' | analyst key) → provider + model actually used. */
  llm_roles?: Record<string, TALlmRole>;
}

export interface TAHandlers {
  onStarted?: (data: TAStarted) => void;
  onNode?: (node: string) => void;
  onReport?: (report: TAReport) => void;
  onDecision?: (decision: TADecision) => void;
  onSaved?: (id: string) => void;
  onError?: (error: unknown) => void;
  onComplete?: () => void;
}

export interface AnalysisSummary {
  id: string;
  symbol: string;
  trade_date: string;
  provider: string;
  model: string;
  signal: string;
  snippet: string;
  duration_ms: number;
  created_at: string;
}

export interface AnalysisDetail {
  id: string;
  symbol: string;
  trade_date: string;
  provider: string;
  model: string;
  signal: string;
  analysts: string[];
  sections: Record<string, string>;
  final_decision: string;
  duration_ms: number;
  created_at: string;
}

export const fetchAnalyses = async (
  symbol?: string,
  limit = 100,
): Promise<AnalysisSummary[]> => {
  const params = new URLSearchParams();
  if (symbol) params.set('symbol', symbol);
  params.set('limit', String(limit));
  const res = await fetch(`${API_BASE_URL}/trading-agents/analyses?${params.toString()}`);
  if (!res.ok) throw new Error(`GET /trading-agents/analyses failed: ${res.status}`);
  const data = (await res.json()) as { analyses: AnalysisSummary[] };
  return data.analyses;
};

export const fetchAnalysis = async (id: string): Promise<AnalysisDetail> =>
  apiGet<AnalysisDetail>(`/trading-agents/analyses/${encodeURIComponent(id)}`);

// Human-friendly labels + display order for the streamed report sections.
export const SECTION_LABELS: Record<string, string> = {
  market: 'Market Analyst',
  sentiment: 'Sentiment Analyst',
  news: 'News Analyst',
  fundamentals: 'Fundamentals Analyst',
  research_debate: 'Bull vs Bear Debate',
  research_manager: 'Investment Plan',
  trader: 'Trader',
  risk_debate: 'Risk Debate',
  final: 'Portfolio Manager — Final Decision',
};

export const SECTION_ORDER: string[] = [
  'market',
  'sentiment',
  'news',
  'fundamentals',
  'research_debate',
  'research_manager',
  'trader',
  'risk_debate',
  'final',
];

export const fetchTradingAgentsHealth = async (): Promise<TAHealth> =>
  apiGet<TAHealth>('/trading-agents/health');

export const fetchModelOptions = async (): Promise<TAModelOptions> =>
  apiGet<TAModelOptions>('/trading-agents/models');

export interface ModelChoice {
  /** What to send: always provider-qualified, so the pick is unambiguous. */
  spec: string;
  provider: string;
  model: string;
}

/**
 * Flatten the catalog into `provider:model` choices for one role tier.
 *
 * Only providers whose API key is present are offered — a run on an
 * unauthenticated provider is rejected by the backend's readiness check — except
 * the default provider, whose catalog is always shown since it is what bare
 * model names resolve to. Providers with no catalog entries (Ollama customs,
 * OpenRouter, …) contribute nothing; those model IDs are typed in freehand.
 */
export const modelChoices = (
  opts: TAModelOptions | null,
  mode: 'quick' | 'deep',
): ModelChoice[] => {
  if (!opts) return [];
  return Object.entries(opts.providers ?? {})
    .filter(([name, p]) => p.ready || name === opts.provider)
    .flatMap(([name, p]) =>
      (p[mode] ?? []).map((model) => ({ spec: `${name}:${model}`, provider: name, model })),
    );
};

/**
 * Start a streaming multi-agent analysis. Parses the SSE stream (both the
 * `event:` and `data:` lines of each block) and dispatches to the handlers.
 * Returns an AbortController so the caller can cancel a long-running run.
 */
export const startAnalysis = (
  payload: AnalyzeRequest,
  handlers: TAHandlers,
): { controller: AbortController; promise: Promise<void> } => {
  const controller = new AbortController();

  const promise = (async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/trading-agents/analyze/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
        },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });

      if (!response.ok) {
        throw new Error(`Analysis stream failed: ${response.status}`);
      }
      if (!response.body) {
        throw new Error('Analysis stream has no body');
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
              handlers.onStarted?.(data as unknown as TAStarted);
              break;
            case 'node':
              handlers.onNode?.(String((data as { node?: string }).node ?? ''));
              break;
            case 'report':
              handlers.onReport?.(data as unknown as TAReport);
              break;
            case 'decision':
              handlers.onDecision?.(data as unknown as TADecision);
              break;
            case 'saved':
              handlers.onSaved?.(String((data as { id?: string }).id ?? ''));
              break;
            case 'error':
              handlers.onError?.(new Error(String((data as { error?: string }).error ?? 'Unknown error')));
              break;
            case 'done':
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
