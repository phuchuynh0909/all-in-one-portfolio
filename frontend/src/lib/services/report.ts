import { API_BASE_URL } from '../api';

export interface Report {
  id: number;
  mack: string | null;
  tenbaocao: string;
  url: string;
  nguon: string;
  ngaykn: string | null;
  rsnganh: string | null;
}

export interface ReportDetail extends Report {
  // Fields from wichart_reports detail table
  clean_content?: string | null;
  llm_summary?: string | null;  // Used for both AI summary and user edits
  recommendation?: string | null;
  report_category?: string | null;
  token_count?: number | null;
  status?: string | null;
}

export interface ReportResponse {
  reports: Report[];
}

/**
 * Report list, optionally filtered by symbol.
 *
 * The endpoint is cached server-side for an hour; pass `refresh` to make the
 * backend recompute it (needed right after adding a report by hand).
 */
export const fetchReports = async (symbol?: string, refresh = false): Promise<Report[]> => {
  const url = new URL(`${API_BASE_URL}/report/list`);
  if (symbol) {
    url.searchParams.append('symbol', symbol);
  }
  const response = await fetch(url.toString(), {
    headers: refresh ? { 'Cache-Control': 'no-cache' } : undefined,
  });
  if (!response.ok) {
    throw new Error('Failed to fetch reports');
  }
  const data: ReportResponse = await response.json();
  return data.reports;
};

export interface ReportCreate {
  /** Feed id to pin; omit to have the backend allocate one. 409 if taken. */
  id?: number | null;
  tenbaocao: string;
  url: string;
  mack?: string | null;
  nguon?: string;
  /** ISO date/datetime string; defaults to now server-side. */
  ngaykn?: string | null;
  rsnganh?: string | null;
}

/** Add a report by hand (for what the crawler missed). */
export const createReport = async (payload: ReportCreate): Promise<Report> => {
  const response = await fetch(`${API_BASE_URL}/report`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    // FastAPI validation errors come back as {detail: [...]}, plain ones as a string.
    let message = `Failed to add report (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body.detail === 'string') {
        message = body.detail;
      } else if (Array.isArray(body.detail) && body.detail.length) {
        message = body.detail
          .map((d: { loc?: (string | number)[]; msg: string }) =>
            `${d.loc?.slice(-1)[0] ?? ''}: ${d.msg}`.trim(),
          )
          .join('; ');
      }
    } catch {
      // Non-JSON body — keep the status-code message.
    }
    throw new Error(message);
  }
  return response.json();
};

export const fetchReportById = async (reportId: number): Promise<ReportDetail> => {
  const response = await fetch(`${API_BASE_URL}/report/${reportId}`);
  if (!response.ok) {
    throw new Error('Failed to fetch report');
  }
  return response.json();
};

export const updateReportSummary = async (reportId: number, summary: string): Promise<void> => {
  const response = await fetch(`${API_BASE_URL}/report/${reportId}/summary`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ summary }),
  });
  if (!response.ok) {
    throw new Error('Failed to save summary');
  }
};

export interface SyncStats {
  total_raw: number;
  existing: number;
  missing: number;
  created: number;
  failed: number;
}

export interface SyncResponse {
  message: string;
  stats: SyncStats;
}

export const syncReports = async (limit: number = 100): Promise<SyncResponse> => {
  const response = await fetch(`${API_BASE_URL}/report/sync?limit=${limit}`, {
    method: 'POST',
  });
  if (!response.ok) {
    throw new Error('Failed to sync reports');
  }
  return response.json();
};

// ---------------------------------------------------------------------------
// RAG pipeline (PDF -> markdown -> embeddings -> Qdrant)
// ---------------------------------------------------------------------------

export type RagStatusValue =
  | 'PENDING'
  | 'PARSING'
  | 'PARSED'
  | 'SUMMARIZING'
  | 'EMBEDDING'
  | 'EMBEDDED'
  | 'FAILED'
  | 'NONE';

export interface RagStatus {
  report_id: number;
  status: RagStatusValue;
  chunk_count?: number;
  updated_at?: string;
  error?: string;
}

export type PdfParser = 'marker' | 'llamaparse' | 'docling' | 'pymupdf4llm';
export const RAG_IN_PROGRESS = ['PENDING', 'PARSING', 'PARSED', 'SUMMARIZING', 'EMBEDDING'];

const pendingSubmissionKeys = new Map<string, string>();

function newSubmissionKey(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/** Trigger the RAG pipeline for a report (runs as a background job). */
export const triggerReportRag = async (
  reportId: number,
  recreate = false,
  parser?: PdfParser,
): Promise<RagStatus> => {
  const params = new URLSearchParams({ recreate: String(recreate) });
  if (parser) params.set('parser', parser);
  const storageKey = `rag-submission:${reportId}:${recreate}:${parser ?? ''}`;
  let key: string;
  try {
    key = sessionStorage.getItem(storageKey) || pendingSubmissionKeys.get(storageKey) || newSubmissionKey();
    pendingSubmissionKeys.set(storageKey, key);
    sessionStorage.setItem(storageKey, key);
  } catch {
    key = pendingSubmissionKeys.get(storageKey) || newSubmissionKey();
    pendingSubmissionKeys.set(storageKey, key);
  }
  const response = await fetch(
    `${API_BASE_URL}/report/${reportId}/rag?${params.toString()}`,
    { method: 'POST', headers: { 'Idempotency-Key': key } },
  );
  if (!response.ok) {
    throw new Error('Failed to queue RAG pipeline');
  }
  const result: RagStatus = await response.json();
  pendingSubmissionKeys.delete(storageKey);
  try { sessionStorage.removeItem(storageKey); } catch { /* Storage may be unavailable. */ }
  return result;
};

/** Bulk RAG status for all tracked reports (keyed by report id). */
export const fetchRagStatuses = async (): Promise<Record<number, RagStatus>> => {
  const response = await fetch(`${API_BASE_URL}/report/rag/statuses`);
  if (!response.ok) {
    throw new Error('Failed to fetch RAG statuses');
  }
  const data: { statuses: RagStatus[] } = await response.json();
  const map: Record<number, RagStatus> = {};
  for (const s of data.statuses) map[s.report_id] = s;
  return map;
};
