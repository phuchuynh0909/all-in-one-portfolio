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