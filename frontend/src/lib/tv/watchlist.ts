/**
 * Watch List API bridge.
 *
 * The charting library's Watch List widget lives in the widget bar and exposes
 * `IWatchListApi` through `widget.watchList()` — but only in the Trading
 * Terminal package. The Advanced Charts build served from `public/` stubs the
 * whole widget bar out: its `library.*.js` compiles to `watchlist(){H()}` where
 * `function H(){throw new Error("not implemented")}`, and `window.widgetbar` is
 * never assigned, so `widgetbar.watchlist: true` renders nothing.
 *
 * So the app speaks only `IWatchListApi`. `resolveWatchListApi` hands back the
 * library's own object when it exists (native widget, app panel steps aside)
 * and otherwise an identical localStorage-backed implementation that drives the
 * app-side panel. Swapping in a Trading Terminal build is then a file drop, not
 * a rewrite.
 *
 * List contents follow the widget's own convention: an item prefixed with `###`
 * is a section divider rather than a symbol.
 */
import type {
  EmptyCallback,
  IChartingLibraryWidget,
  ISubscription,
  IWatchListApi,
  WatchListSymbolList,
  WatchListSymbolListAddedCallback,
  WatchListSymbolListChangedCallback,
  WatchListSymbolListMap,
  WatchListSymbolListRemovedCallback,
  WatchListSymbolListRenamedCallback,
} from './charting_library';

/** Prefix marking a list item as a section divider instead of a symbol. */
export const SECTION_PREFIX = '###';

export function isSectionDivider(item: string): boolean {
  return item.startsWith(SECTION_PREFIX);
}

/** Section title without the `###` marker. */
export function sectionTitle(item: string): string {
  return item.slice(SECTION_PREFIX.length).trim();
}

/** Only the tradable entries of a list (sections dropped). */
export function listSymbols(items: string[]): string[] {
  return items.filter((item) => !isSectionDivider(item));
}

/** Symbols the app starts with when nothing is stored yet. */
export const DEFAULT_WATCHLIST_SYMBOLS = ['VNINDEX', 'VCG', 'SHS', 'HPG'];

const STORAGE_KEY = 'chartWatchlists';
/** Pre-multi-list key: a bare `string[]` of symbols. Migrated on first read. */
const LEGACY_STORAGE_KEY = 'chartWatchlist';

const DEFAULT_LIST_ID = 'default';
const DEFAULT_LIST_TITLE = 'Watchlist';

interface StoredState {
  lists: WatchListSymbolListMap;
  activeId: string;
}

/** `ISubscription` plus the `fire` the library keeps to itself. */
class Emitter<TFunc extends (...args: never[]) => void> implements ISubscription<TFunc> {
  private handlers: { obj: object | null; member: TFunc; singleshot: boolean }[] = [];

  subscribe(obj: object | null, member: TFunc, singleshot = false): void {
    this.handlers.push({ obj, member, singleshot });
  }

  unsubscribe(obj: object | null, member: TFunc): void {
    this.handlers = this.handlers.filter((h) => !(h.obj === obj && h.member === member));
  }

  unsubscribeAll(obj: object | null): void {
    this.handlers = this.handlers.filter((h) => h.obj !== obj);
  }

  fire(...args: Parameters<TFunc>): void {
    // Snapshot first: a handler may (un)subscribe while the event is firing.
    for (const handler of [...this.handlers]) {
      if (handler.singleshot) this.unsubscribe(handler.obj, handler.member);
      try {
        handler.member.apply(handler.obj, args);
      } catch (e) {
        console.warn('Watch list subscriber threw:', e);
      }
    }
  }
}

/**
 * Normalises a list the way the widget would: symbols upper-cased and
 * de-duplicated, section dividers kept verbatim (and repeatable).
 */
function normaliseItems(items: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of items) {
    if (typeof raw !== 'string') continue;
    const item = raw.trim();
    if (!item) continue;
    if (isSectionDivider(item)) {
      out.push(item);
      continue;
    }
    const symbol = item.toUpperCase();
    if (seen.has(symbol)) continue;
    seen.add(symbol);
    out.push(symbol);
  }
  return out;
}

function defaultState(): StoredState {
  return {
    lists: {
      [DEFAULT_LIST_ID]: {
        id: DEFAULT_LIST_ID,
        title: DEFAULT_LIST_TITLE,
        symbols: [...DEFAULT_WATCHLIST_SYMBOLS],
      },
    },
    activeId: DEFAULT_LIST_ID,
  };
}

function readState(): StoredState {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) {
      const parsed = JSON.parse(stored) as Partial<StoredState>;
      const lists = parsed.lists;
      if (lists && typeof lists === 'object') {
        // Trust the stored shape only as far as the fields read back here.
        const clean: WatchListSymbolListMap = {};
        for (const [id, list] of Object.entries(lists)) {
          if (!list || !Array.isArray(list.symbols)) continue;
          clean[id] = { id, title: String(list.title ?? id), symbols: normaliseItems(list.symbols) };
        }
        const ids = Object.keys(clean);
        if (ids.length > 0) {
          const activeId = parsed.activeId && clean[parsed.activeId] ? parsed.activeId : ids[0];
          return { lists: clean, activeId };
        }
      }
    }

    // Migrate the single pre-multi-list array, leaving the old key in place so
    // an older deployment on this origin still finds its symbols.
    const legacy = localStorage.getItem(LEGACY_STORAGE_KEY);
    if (legacy) {
      const parsed: unknown = JSON.parse(legacy);
      if (Array.isArray(parsed)) {
        const state = defaultState();
        // An empty stored array is a real state (the user removed everything).
        state.lists[DEFAULT_LIST_ID].symbols = normaliseItems(parsed as string[]);
        return state;
      }
    }
  } catch { /* corrupt / private mode — fall through to defaults */ }
  return defaultState();
}

let idCounter = 0;
function nextListId(): string {
  idCounter += 1;
  return `list-${Date.now().toString(36)}-${idCounter}`;
}

/**
 * localStorage-backed `IWatchListApi`, method-for-method with the widget's own.
 */
function createLocalWatchListApi(): IWatchListApi {
  let state = readState();

  const listChanged = new Emitter<WatchListSymbolListChangedCallback>();
  const activeListChanged = new Emitter<EmptyCallback>();
  const listAdded = new Emitter<WatchListSymbolListAddedCallback>();
  const listRemoved = new Emitter<WatchListSymbolListRemovedCallback>();
  const listRenamed = new Emitter<WatchListSymbolListRenamedCallback>();

  const persist = () => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch { /* quota / private mode — in-memory state stays correct */ }
  };

  const copy = (list: WatchListSymbolList): WatchListSymbolList => ({
    ...list,
    symbols: [...list.symbols],
  });

  return {
    defaultList: () => [...DEFAULT_WATCHLIST_SYMBOLS],

    getList: (id?: string) => {
      const list = state.lists[id ?? state.activeId];
      return list ? [...list.symbols] : null;
    },

    getAllLists: () => {
      const all: WatchListSymbolListMap = {};
      for (const [id, list] of Object.entries(state.lists)) all[id] = copy(list);
      return all;
    },

    getActiveListId: () => state.activeId,

    setActiveList: (id: string) => {
      if (!state.lists[id] || id === state.activeId) return;
      state = { ...state, activeId: id };
      persist();
      activeListChanged.fire();
    },

    /** Obsolete in the library's own API, but callers may still reach for it. */
    setList: (symbols: string[]) => {
      const list = state.lists[state.activeId];
      if (!list) return;
      state.lists[state.activeId] = { ...list, symbols: normaliseItems(symbols) };
      persist();
      listChanged.fire(state.activeId);
    },

    updateList: (listId: string, symbols: string[]) => {
      const list = state.lists[listId];
      if (!list) return;
      state.lists[listId] = { ...list, symbols: normaliseItems(symbols) };
      persist();
      listChanged.fire(listId);
    },

    renameList: (listId: string, newName: string) => {
      const list = state.lists[listId];
      const title = newName.trim();
      if (!list || !title || title === list.title) return;
      const oldName = list.title;
      state.lists[listId] = { ...list, title };
      persist();
      listRenamed.fire(listId, oldName, title);
    },

    createList: (listName?: string, symbols: string[] = []) => {
      if (!listName?.trim()) return null;
      const list: WatchListSymbolList = {
        id: nextListId(),
        title: listName.trim(),
        symbols: normaliseItems(symbols),
      };
      state.lists[list.id] = list;
      state = { ...state, activeId: list.id };
      persist();
      listAdded.fire(list.id, [...list.symbols]);
      activeListChanged.fire();
      return copy(list);
    },

    saveList: (list: WatchListSymbolList) => {
      const title = list.title?.trim();
      if (!title) return false;
      const symbols = normaliseItems(list.symbols);
      const duplicate = Object.values(state.lists).some(
        (existing) => existing.title === title && existing.symbols.join(' ') === symbols.join(' '),
      );
      if (duplicate) return false;
      const saved: WatchListSymbolList = { id: list.id || nextListId(), title, symbols };
      const isNew = !state.lists[saved.id];
      state.lists[saved.id] = saved;
      persist();
      if (isNew) listAdded.fire(saved.id, [...saved.symbols]);
      else listChanged.fire(saved.id);
      return true;
    },

    deleteList: (listId: string) => {
      if (!state.lists[listId]) return;
      // The widget always keeps one list around; deleting the last one would
      // leave the panel with nothing to render.
      if (Object.keys(state.lists).length === 1) return;
      delete state.lists[listId];
      if (state.activeId === listId) state.activeId = Object.keys(state.lists)[0];
      persist();
      listRemoved.fire(listId);
      activeListChanged.fire();
    },

    onListChanged: () => listChanged,
    onActiveListChanged: () => activeListChanged,
    onListAdded: () => listAdded,
    onListRemoved: () => listRemoved,
    onListRenamed: () => listRenamed,
  };
}

let localApi: IWatchListApi | null = null;

/**
 * The app-side `IWatchListApi` singleton. Deliberately shared: every consumer
 * must see the same lists, the way they would when the library owns the state.
 */
export function getLocalWatchListApi(): IWatchListApi {
  if (!localApi) localApi = createLocalWatchListApi();
  return localApi;
}

export interface ResolvedWatchList {
  api: IWatchListApi;
  /**
   * True when `api` is the library's own Watch List widget (Trading Terminal).
   * The app panel must then stay hidden — the widget bar already shows one.
   */
  native: boolean;
}

/**
 * Prefers the library's Watch List widget, falls back to the app-side list.
 *
 * On Advanced Charts `watchList()` throws (`new Error("not implemented")`),
 * which is the signal that the widget bar is absent from this build.
 */
export async function resolveWatchListApi(
  widget: IChartingLibraryWidget,
): Promise<ResolvedWatchList> {
  try {
    const api = await widget.watchList();
    if (api) return { api, native: true };
  } catch { /* Advanced Charts build — no widget bar */ }
  return { api: getLocalWatchListApi(), native: false };
}
