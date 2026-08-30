import { apiGet } from '../api';

export interface PeriodSummary {
  label: string;
  end_date: string;
  period_type: string;
}

export interface FinancialStatementItem {
  item_id: number;
  item_key: string;
  title_vi: string;
  level: number;
  parent_item_id: number | null;
  display_order: number | null;
  values: Record<string, number | null>;
}

export interface FinancialStatement {
  statement_type: string;
  title: string;
  items: FinancialStatementItem[];
}

export interface FinancialStatementResponse {
  company_ticker: string;
  company_name: string;
  periods: PeriodSummary[];
  statements: FinancialStatement[];
}

export interface StatementSummary {
  statement_type: string;
  title: string;
  period_count: number;
  item_count: number;
  earliest_period: string;
  latest_period: string;
}

export interface CompanyStatementsSummary {
  company_ticker: string;
  company_name: string;
  statements: StatementSummary[];
}

export interface Period {
  label: string;
  period_type: string;
  start_date: string;
  end_date: string;
}

export interface CompanyWithFinancialData {
  ticker: string;
  name: string;
  data_points: number;
  periods_count: number;
  earliest_period: string;
  latest_period: string;
}

export const financialApi = {
  async getFinancialStatements(
    ticker: string,
    statementTypes?: string[],
    periods?: string[],
    maxPeriods = 8,
    maxLevel = 5
  ): Promise<FinancialStatementResponse> {
    const params = new URLSearchParams();
    
    if (statementTypes && statementTypes.length > 0) {
      statementTypes.forEach(type => params.append('statement_types', type));
    }
    
    if (periods && periods.length > 0) {
      periods.forEach(period => params.append('periods', period));
    }
    
    params.append('max_periods', maxPeriods.toString());
    params.append('max_level', maxLevel.toString());

    const response = await apiGet<FinancialStatementResponse>(
      `/financial/companies/${ticker}/statements?${params.toString()}`
    );
    return response;
  },

  async getStatementsSummary(ticker: string): Promise<CompanyStatementsSummary> {
    const response = await apiGet<CompanyStatementsSummary>(`/financial/companies/${ticker}/statements/summary`);
    return response;
  },

  async getAvailablePeriods(): Promise<Period[]> {
    const response = await apiGet<Period[]>('/financial/periods');
    return response;
  },

  async getCompaniesWithFinancialData(): Promise<CompanyWithFinancialData[]> {
    const response = await apiGet<CompanyWithFinancialData[]>('/financial/companies');
    return response;
  },
};
