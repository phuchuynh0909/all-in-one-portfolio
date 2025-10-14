import { apiGet, apiPost } from '../api';

export type ConditionOperator = 'eq' | 'ne' | 'gt' | 'gte' | 'lt' | 'lte' | 'in' | 'notin' | 'between' | 'contains';

export interface Condition {
  column: string;
  operator: ConditionOperator;
  value: unknown;
}

export interface ScannerRequest {
  conditions: Condition[];
  columns_to_return?: string[];
  start_date?: string; // yyyy-mm-dd
  end_date?: string;   // yyyy-mm-dd
  symbols?: string[];
  latest_only?: boolean;
}

export interface ScannerResultItem {
  symbol: string;
  date: string;
  values: Record<string, unknown>;
}

export interface ScannerResponse {
  items: ScannerResultItem[];
  total: number;
}

export interface ScannerColumnsResponse {
  columns: string[];
}

export async function getScannerColumns(): Promise<string[]> {
  const res = await apiGet<ScannerColumnsResponse>('/scanner/columns');
  return res.columns;
}

export async function scanFeatures(req: ScannerRequest): Promise<ScannerResponse> {
  return await apiPost<ScannerResponse>('/scanner/scan', req);
}


