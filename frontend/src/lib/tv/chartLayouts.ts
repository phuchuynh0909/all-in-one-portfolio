/** A named chart setup, including indicator membership, settings and pane state. */
export interface ChartLayout {
  id: string;
  name: string;
  indicators: string[];
  params?: Record<string, Record<string, number>>;
  scripts?: string[];
  chartState?: object;
}

export const CUSTOM_LAYOUTS_KEY = 'chartCustomLayouts.v1';

export function readCustomLayouts(storage: Pick<Storage, 'getItem'>): ChartLayout[] {
  const stored: unknown = JSON.parse(storage.getItem(CUSTOM_LAYOUTS_KEY) ?? '[]');
  return parseChartLayouts(stored, false);
}

export function parseChartLayouts(stored: unknown, allowBuiltIn = true): ChartLayout[] {
  if (!Array.isArray(stored)) throw new Error('Saved layouts are not a list.');
  const ids = new Set<string>();
  return stored.map((value: unknown) => {
    if (!value || typeof value !== 'object') throw new Error('Invalid saved layout.');
    const layout = value as Record<string, unknown>;
    if (typeof layout.id !== 'string' || !(allowBuiltIn ? /^(layout[123]|custom_[a-zA-Z0-9_-]+)$/ : /^custom_[a-zA-Z0-9_-]+$/).test(layout.id) || ids.has(layout.id)
      || typeof layout.name !== 'string' || !layout.name.trim() || layout.name.length > 60
      || !Array.isArray(layout.indicators) || !layout.indicators.every((id) => typeof id === 'string')
      || !Array.isArray(layout.scripts) || !layout.scripts.every((id) => typeof id === 'string')
      || !layout.params || typeof layout.params !== 'object' || Array.isArray(layout.params)
      || (layout.chartState !== undefined && (!layout.chartState || typeof layout.chartState !== 'object' || Array.isArray(layout.chartState)))
      || !Object.values(layout.params).every((params) => params && typeof params === 'object' && !Array.isArray(params)
        && Object.values(params).every((param) => typeof param === 'number' && Number.isFinite(param)))) {
      throw new Error('Invalid saved layout.');
    }
    ids.add(layout.id);
    return { id: layout.id, name: layout.name.trim(), indicators: layout.indicators, scripts: layout.scripts, params: layout.params as ChartLayout['params'], chartState: layout.chartState as object | undefined };
  });
}
