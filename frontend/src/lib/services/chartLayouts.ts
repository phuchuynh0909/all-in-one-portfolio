import { apiDelete, apiGet, apiPatch, apiPost, apiPut } from '../api';
import { parseChartLayouts, type ChartLayout } from '../tv/chartLayouts';

interface SavedLayout {
  id: string;
  name: string;
  indicators: string[];
  params: Record<string, Record<string, number>>;
  scripts: string[];
  chart_state: object | null;
}

interface SavedLibrary {
  layouts: SavedLayout[];
  active_layout_id: string | null;
}

export interface ChartLayoutLibrary {
  layouts: ChartLayout[];
  activeLayoutId: string | null;
}

function decode(layouts: SavedLayout[]): ChartLayout[] {
  return parseChartLayouts(layouts.map((layout) => ({ ...layout, chartState: layout.chart_state ?? undefined })));
}

function encode(layout: ChartLayout): SavedLayout {
  return { id: layout.id, name: layout.name, indicators: layout.indicators, params: layout.params ?? {}, scripts: layout.scripts ?? [], chart_state: layout.chartState ?? null };
}

function library(value: SavedLibrary): ChartLayoutLibrary {
  return { layouts: decode(value.layouts), activeLayoutId: value.active_layout_id };
}

export async function listChartLayouts(): Promise<ChartLayoutLibrary> {
  return library(await apiGet<SavedLibrary>('/chart-layouts'));
}

export async function saveChartLayout(layout: ChartLayout): Promise<ChartLayout> {
  const { id, ...body } = encode(layout);
  return decode([await apiPut<SavedLayout>(`/chart-layouts/${encodeURIComponent(id)}`, body)])[0];
}

export async function selectChartLayout(id: string): Promise<ChartLayout> {
  return decode([await apiPost<SavedLayout>('/chart-layouts/active', { layout_id: id })])[0];
}

export async function renameChartLayout(id: string, name: string): Promise<ChartLayout> {
  return decode([await apiPatch<SavedLayout>(`/chart-layouts/${encodeURIComponent(id)}`, { name })])[0];
}

export async function deleteChartLayout(id: string): Promise<ChartLayoutLibrary> {
  return library(await apiDelete<SavedLibrary>(`/chart-layouts/${encodeURIComponent(id)}`));
}

export async function importChartLayouts(layouts: ChartLayout[], activeLayoutId: string | null): Promise<ChartLayoutLibrary> {
  return library(await apiPost<SavedLibrary>('/chart-layouts/import', { layouts: layouts.map(encode), active_layout_id: activeLayoutId }));
}
