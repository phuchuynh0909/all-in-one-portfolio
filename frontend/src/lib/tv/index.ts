/**
 * Runtime loader for the TradingView Advanced Charting Library.
 *
 * The library ships as pre-built ESM whose 500+ runtime chunks are loaded lazily
 * from `${library_path}bundles/` at runtime (webpack public-path style). We serve
 * the whole `charting_library/` folder statically from `public/` (→ `/charting_library/`)
 * and import the `widget` class from the served ESM via a `@vite-ignore`d dynamic
 * import so Vite does not attempt to bundle the pre-built file.
 *
 * Types come from the sibling `charting_library.d.ts` (kept in `src`, self-contained).
 */
import type {
  ChartingLibraryWidgetOptions,
  IChartingLibraryWidget,
  TradingTerminalWidgetOptions,
} from './charting_library';

/**
 * Constructor options for either package. Trading Terminal-only fields (notably
 * `widgetbar`, which enables the Watch List widget) are accepted so the same
 * call site works if those library files are ever dropped in; the Advanced
 * Charts build simply ignores them.
 */
export type TvWidgetOptions = ChartingLibraryWidgetOptions | TradingTerminalWidgetOptions;

/** Base URL where `public/charting_library/` is served from. */
export const LIBRARY_PATH = '/charting_library/';

type ChartingLibraryModule = {
  widget: new (options: TvWidgetOptions) => IChartingLibraryWidget;
  version: string;
};

let modulePromise: Promise<ChartingLibraryModule> | null = null;

/** Loads (once) and returns the raw charting-library ESM module. */
export function loadChartingLibrary(): Promise<ChartingLibraryModule> {
  if (!modulePromise) {
    modulePromise = import(
      /* @vite-ignore */ `${LIBRARY_PATH}charting_library.esm.js`
    ) as Promise<ChartingLibraryModule>;
  }
  return modulePromise;
}

/** Constructs a widget instance, loading the library on first use. */
export async function createTvWidget(
  options: TvWidgetOptions,
): Promise<IChartingLibraryWidget> {
  const { widget: Widget } = await loadChartingLibrary();
  return new Widget(options);
}

export type * from './charting_library';
