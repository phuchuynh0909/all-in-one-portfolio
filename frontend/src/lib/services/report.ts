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

export const fetchReports = async (symbol?: string): Promise<Report[]> => {
  const url = new URL(`${API_BASE_URL}/report/list`);
  if (symbol) {
    url.searchParams.append('symbol', symbol);
  }
  const response = await fetch(url.toString());
  if (!response.ok) {
    throw new Error('Failed to fetch reports');
  }
  const data: ReportResponse = await response.json();
  return data.reports;
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