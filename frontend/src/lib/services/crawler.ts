import { apiGet, apiPost } from '../api';

export interface CrawlableSymbol {
  ticker: string;
  name: string;
  data_count: number;
  needs_crawling: boolean;
}

export interface CrawlStatus {
  symbol: string;
  company_name: string;
  data_count: number;
  has_data: boolean;
  status: 'completed' | 'pending';
}

export interface CrawlResponse {
  status: 'started' | 'completed' | 'skipped';
  message: string;
  company: {
    ticker: string;
    name: string;
  };
}

export const crawlerApi = {
  async getAvailableSymbols(): Promise<CrawlableSymbol[]> {
    const response = await apiGet<CrawlableSymbol[]>('/crawler/available-symbols');
    return response;
  },

  async crawlSymbol(symbol: string, quarter = 1, refresh = false): Promise<CrawlResponse> {
    const params = new URLSearchParams({
      quarter: quarter.toString(),
      refresh: refresh.toString(),
    });
    const response = await apiPost<CrawlResponse>(`/crawler/crawl-symbol/${symbol}?${params.toString()}`, {});
    return response;
  },

  async getCrawlStatus(symbol: string): Promise<CrawlStatus> {
    const response = await apiGet<CrawlStatus>(`/crawler/crawl-status/${symbol}`);
    return response;
  },
};
