import { apiGet, apiPost } from '../api';
import type { SectorRotation, SectorRotationRequest, SectorRsTimeframe } from './timeseries';

export interface SymbolStrengthBatch {
  benchmark: string;
  window: number;
  momentum_window: number;
  timeframe: SectorRsTimeframe;
  dates: string[];
  rows: {
    symbol: string;
    rs: (number | null)[];
    ratio: (number | null)[];
    momentum: (number | null)[];
    unavailable: string | null;
  }[];
}

export const fetchSymbolRelativeStrength = (symbols: string[], params: SectorRotationRequest & { benchmark?: string } = {}) =>
  apiPost<SymbolStrengthBatch>('/timeseries/symbols/relative-strength', { symbols, ...params });

export async function fetchSectorSymbolStrength(level: number, sectorId: number, params: SectorRotationRequest = {}): Promise<SectorRotation> {
  const symbols = await apiGet<{ symbol: string }[]>(`/sector/symbols/${level}/${sectorId}`);
  if (!symbols.length) return {
    sector_level: String(level), benchmark: 'VNINDEX', window: params.window ?? 50,
    momentum_window: params.momentum_window ?? 10, timeframe: params.timeframe ?? 'daily', dates: [], rows: [],
  };
  const batch = await fetchSymbolRelativeStrength(symbols.map((stock) => stock.symbol), params);
  return {
    ...batch, sector_level: String(level),
    unavailable: batch.rows.filter((row) => row.unavailable).map((row) => ({ symbol: row.symbol, reason: row.unavailable! })),
    rows: batch.rows.filter((row) => !row.unavailable).map((row, index) => ({
      id: index, name: row.symbol, ratio: row.ratio, momentum: row.momentum,
    })).sort((a, b) => {
      const order = (row: typeof a) => {
        let i = row.ratio.length - 1;
        while (i >= 0 && (row.ratio[i] == null || row.momentum[i] == null)) i -= 1;
        const x = row.ratio[i] ?? 100;
        const y = row.momentum[i] ?? 100;
        return { quadrant: y >= 100 ? (x >= 100 ? 0 : 1) : (x >= 100 ? 2 : 3), distance: (x - 100) ** 2 + (y - 100) ** 2 };
      };
      const first = order(a), second = order(b);
      return first.quadrant - second.quadrant || second.distance - first.distance;
    }),
  };
}
