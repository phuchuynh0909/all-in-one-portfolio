/**
 * Account-owned Watch List API bridge. The chart panel uses TradingView's
 * IWatchListApi contract while MySQL stores lists independently of layouts.
 * Browser storage is read only to offer migration of older watchlists.
 */
import { getToken } from '../auth/token';
import { createWatchlistTransport, type WatchlistLibrary, type WatchlistTransport, type WatchlistWrite } from '../services/watchlists';
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

export interface StoredState {
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

export function readLegacyWatchlists(storage: Pick<Storage, 'getItem'>): StoredState | null {
  try {
    const stored = storage.getItem(STORAGE_KEY);
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
    const legacy = storage.getItem(LEGACY_STORAGE_KEY);
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
  return null;
}

let idCounter = 0;
function nextListId(): string {
  idCounter += 1;
  return `list-${Date.now().toString(36)}-${idCounter}`;
}

/**
 * In-memory `IWatchListApi`; mutations are persisted by the server connection.
 */
function createWatchListApi(initial: StoredState, onChange: (state: StoredState) => void) {
  let state = initial;

  const listChanged = new Emitter<WatchListSymbolListChangedCallback>();
  const activeListChanged = new Emitter<EmptyCallback>();
  const listAdded = new Emitter<WatchListSymbolListAddedCallback>();
  const listRemoved = new Emitter<WatchListSymbolListRemovedCallback>();
  const listRenamed = new Emitter<WatchListSymbolListRenamedCallback>();

  const persist = () => onChange(snapshot());

  const copy = (list: WatchListSymbolList): WatchListSymbolList => ({
    ...list,
    symbols: [...list.symbols],
  });

  const snapshot = (): StoredState => ({ lists: Object.fromEntries(Object.entries(state.lists).map(([id, list]) => [id, copy(list)])), activeId: state.activeId });

  const api: IWatchListApi = {
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
      if (!state.activeId || !listName?.trim()) return null;
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
      if (!state.activeId || !title) return false;
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
  return {
    api, snapshot,
    hydrate: (next: StoredState) => {
      const previous = state;
      state = next;
      for (const id of Object.keys(previous.lists)) if (!next.lists[id]) listRemoved.fire(id);
      for (const [id, list] of Object.entries(next.lists)) {
        if (!previous.lists[id]) listAdded.fire(id, [...list.symbols]);
        else if (previous.lists[id].title !== list.title) listRenamed.fire(id, previous.lists[id].title, list.title);
        listChanged.fire(id);
      }
      activeListChanged.fire();
    },
  };
}

export interface WatchlistStatus {
  phase: 'loading' | 'saving' | 'saved' | 'error';
  ready: boolean;
  message: string;
  canImport: boolean;
}

export interface WatchlistPersistence {
  getStatus(): WatchlistStatus;
  onStatusChanged(): ISubscription<EmptyCallback>;
  retry(): void;
  importBrowserLists(): Promise<void>;
}

export interface ResolvedWatchList {
  api: IWatchListApi;
  native: boolean;
  persistence: WatchlistPersistence;
  dispose(): void;
}

function decodeLibrary(library: WatchlistLibrary): StoredState {
  if (!Array.isArray(library.lists) || !library.lists.length) throw new Error('The server returned no watchlists.');
  const lists = Object.fromEntries(library.lists.map((list) => [list.id, { ...list, symbols: normaliseItems(list.symbols) }]));
  if (!library.active_list_id || !lists[library.active_list_id]) throw new Error('The selected server watchlist does not exist.');
  return { lists, activeId: library.active_list_id };
}

function encodeState(state: StoredState): WatchlistWrite {
  return { lists: Object.values(state.lists).map((list) => ({ ...list, symbols: [...list.symbols] })), active_list_id: state.activeId };
}

/**
 * Synchronous UI edits are queued in order; only a confirmed server response is
 * reported as saved. Failed writes remain in memory for Retry, never on disk.
 */
export function createServerWatchListApi(
  transport: WatchlistTransport = createWatchlistTransport(),
  legacy: StoredState | null = (() => { try { return readLegacyWatchlists(localStorage); } catch { return null; } })(),
): ResolvedWatchList {
  let disposed = false;
  let loaded = false;
  let revision = 0;
  let importedIds: string[] = [];
  let queue: Promise<void> = Promise.resolve();
  let status: WatchlistStatus = { phase: 'loading', ready: false, message: 'Loading watchlists…', canImport: false };
  const statusChanged = new Emitter<EmptyCallback>();
  const report = (phase: WatchlistStatus['phase'], message: string) => {
    if (disposed) return;
    const server = store.api.getAllLists() ?? {};
    const canImport = Boolean(legacy && Object.values(legacy.lists).some((list) => !importedIds.includes(list.id)
      && JSON.stringify(server[list.id]) !== JSON.stringify(list)));
    status = { phase, ready: loaded, message, canImport };
    statusChanged.fire();
  };
  const fail = (error: unknown) => report('error', error instanceof Error ? error.message : 'Could not save watchlists.');
  const persist = (snapshot: StoredState): Promise<void> => {
    const current = ++revision;
    report('saving', 'Saving watchlist…');
    const operation = queue.then(async () => {
      if (disposed) return;
      const saved = await transport.save(encodeState(snapshot));
      if (disposed) return;
      importedIds = saved.imported_ids;
      if (revision === current) {
        store.hydrate(decodeLibrary(saved));
        report('saved', 'Watchlist saved to server');
      }
    });
    queue = operation.catch((error) => { if (revision === current) fail(error); });
    return operation;
  };
  const store = createWatchListApi({ lists: {}, activeId: '' }, (snapshot) => {
    if (!loaded || disposed) return;
    void persist(snapshot).catch(() => {});
  });
  const load = async () => {
    report('loading', 'Loading watchlists…');
    try {
      let library = await transport.load();
      if (disposed) return;
      if (!library.lists.length) library = await transport.initialize(encodeState(defaultState()));
      if (disposed) return;
      importedIds = library.imported_ids;
      store.hydrate(decodeLibrary(library));
      loaded = true;
      report('saved', 'Watchlist saved to server');
    } catch (error) { fail(error); }
  };
  void load();
  return {
    api: store.api, native: false,
    persistence: {
      getStatus: () => status,
      onStatusChanged: () => statusChanged,
      retry: () => { if (!disposed) { if (!loaded) void load(); else void persist(store.snapshot()).catch(() => {}); } },
      importBrowserLists: async () => {
        if (!legacy || !loaded || disposed || status.phase === 'error') return;
        report('saving', 'Importing browser watchlists…');
        try {
          await queue;
          if (disposed) return;
          const library = await transport.import(encodeState(legacy));
          if (disposed) return;
          importedIds = library.imported_ids;
          store.hydrate(decodeLibrary(library));
          report('saved', 'Browser watchlists imported to server');
        } catch (error) { fail(error); }
      },
    },
    dispose: () => { disposed = true; transport.dispose(); },
  };
}

// Reuse one connection across widget/layout rebuilds, scoped to the login.
let cached: { token: string | null; connection: ResolvedWatchList } | null = null;

export function resetWatchListConnection(): void {
  cached?.connection.dispose();
  cached = null;
}

export async function resolveWatchListApi(_widget: IChartingLibraryWidget): Promise<ResolvedWatchList> {
  const token = getToken();
  if (!cached || cached.token !== token) {
    resetWatchListConnection();
    cached = { token, connection: createServerWatchListApi() };
  }
  return cached.connection;
}
