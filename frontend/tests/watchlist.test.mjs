import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import test, { after } from 'node:test';
import { build } from 'esbuild';

const result = await build({ entryPoints: ['src/lib/tv/watchlist.ts'], bundle: true, write: false, format: 'esm', platform: 'node', define: { 'import.meta.env': '{}' } });
const directory = await mkdtemp(join(tmpdir(), 'watchlist-tests-'));
after(() => rm(directory, { recursive: true, force: true }));
const path = join(directory, 'watchlist.mjs');
await writeFile(path, result.outputFiles[0].text);
const { createServerWatchListApi, readLegacyWatchlists } = await import(pathToFileURL(path).href);

const library = (symbols = ['HPG']) => ({ lists: [{ id: 'default', title: 'Watchlist', symbols }], active_list_id: 'default', imported_ids: [] });
const copy = (value) => structuredClone(value);
function transport(initial = library()) {
  let state = copy(initial);
  const saves = [];
  return {
    saves,
    load: async () => copy(state),
    initialize: async (next) => { if (!state.lists.length) state = { ...copy(next), imported_ids: [] }; return copy(state); },
    save: async (next) => { saves.push(copy(next)); state = { ...copy(next), imported_ids: state.imported_ids }; return copy(state); },
    import: async (next) => { state = { ...copy(next), imported_ids: next.lists.map((list) => list.id) }; return copy(state); },
    dispose: () => {},
  };
}

function until(connection, predicate = (status) => status.ready && status.phase === 'saved') {
  if (predicate(connection.persistence.getStatus())) return Promise.resolve();
  return new Promise((resolve) => {
    const owner = {};
    connection.persistence.onStatusChanged().subscribe(owner, () => {
      if (predicate(connection.persistence.getStatus())) {
        connection.persistence.onStatusChanged().unsubscribeAll(owner);
        resolve();
      }
    });
  });
}

test('server lists load before defaults and preserve empty lists and selection', { timeout: 2000 }, async () => {
  const initial = { lists: [{ id: 'empty', title: 'Empty', symbols: [] }, { id: 'research', title: 'Research', symbols: ['FPT'] }], active_list_id: 'empty', imported_ids: [] };
  const connection = createServerWatchListApi(transport(initial), null);
  assert.deepEqual(connection.api.getAllLists(), {});
  await until(connection);
  assert.equal(connection.api.getActiveListId(), 'empty');
  assert.deepEqual(connection.api.getList(), []);
  assert.equal(Object.keys(connection.api.getAllLists()).length, 2);
  connection.dispose();
});

test('rapid edits save snapshots in order with named lists and repeated sections', { timeout: 2000 }, async () => {
  const remote = transport();
  const connection = createServerWatchListApi(remote, null);
  await until(connection);
  connection.api.updateList('default', [' fpt ', 'HPG', 'FPT', '###Research', '###Research']);
  const created = connection.api.createList('Research', ['vcg']);
  connection.api.renameList(created.id, 'Ideas');
  await until(connection);
  assert.equal(remote.saves.length, 3);
  assert.deepEqual(remote.saves[0].lists[0].symbols, ['FPT', 'HPG', '###Research', '###Research']);
  assert.equal(remote.saves[1].lists[1].title, 'Research');
  assert.equal(remote.saves[2].lists[1].title, 'Ideas');
  assert.equal(remote.saves[2].active_list_id, created.id);
  connection.dispose();
});

test('failed writes remain unsaved until retry succeeds and reload uses server data', { timeout: 2000 }, async () => {
  const remote = transport();
  const save = remote.save;
  let fail = true;
  remote.save = async (state) => { if (fail) throw new Error('Database unavailable'); return save(state); };
  const connection = createServerWatchListApi(remote, null);
  await until(connection);
  connection.api.updateList('default', ['FPT']);
  await until(connection, (status) => status.phase === 'error');
  assert.match(connection.persistence.getStatus().message, /Database unavailable/);
  assert.deepEqual(connection.api.getList(), ['FPT']);
  assert.deepEqual((await remote.load()).lists[0].symbols, ['HPG']);
  fail = false;
  connection.persistence.retry();
  await until(connection);
  connection.dispose();
  const reloaded = createServerWatchListApi(remote, null);
  await until(reloaded);
  assert.deepEqual(reloaded.api.getList(), ['FPT']);
  reloaded.dispose();
});

test('failed initial loads show an error without falling back to browser data', { timeout: 2000 }, async () => {
  const remote = transport();
  const load = remote.load;
  remote.load = async () => { throw new Error('Offline'); };
  const connection = createServerWatchListApi(remote, { lists: { old: { id: 'old', title: 'Browser', symbols: ['SECRET'] } }, activeId: 'old' });
  await until(connection, (status) => status.phase === 'error');
  assert.equal(connection.persistence.getStatus().ready, false);
  assert.deepEqual(connection.api.getAllLists(), {});
  remote.load = load;
  connection.persistence.retry();
  await until(connection);
  assert.deepEqual(connection.api.getList(), ['HPG']);
  connection.dispose();
});

test('legacy empty lists can be imported once and remain empty', { timeout: 2000 }, async () => {
  const legacy = readLegacyWatchlists({ getItem: (key) => key === 'chartWatchlist' ? '[]' : null });
  assert.deepEqual(legacy.lists.default.symbols, []);
  const connection = createServerWatchListApi(transport(), legacy);
  await until(connection);
  assert.equal(connection.persistence.getStatus().canImport, true);
  await connection.persistence.importBrowserLists();
  assert.deepEqual(connection.api.getList(), []);
  assert.equal(connection.persistence.getStatus().canImport, false);
  connection.dispose();
});

test('disposing a login aborts its connection and prevents queued writes', { timeout: 2000 }, async () => {
  const remote = transport();
  let release;
  let started;
  const startedPromise = new Promise((resolve) => { started = resolve; });
  const gate = new Promise((resolve) => { release = resolve; });
  const save = remote.save;
  remote.save = async (state) => { started(); await gate; return save(state); };
  let disposed = false;
  remote.dispose = () => { disposed = true; };
  const connection = createServerWatchListApi(remote, null);
  await until(connection);
  connection.api.updateList('default', ['FPT']);
  await startedPromise;
  connection.api.updateList('default', ['VCG']);
  connection.dispose();
  release();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(disposed, true);
  assert.equal(remote.saves.length, 1);
  assert.deepEqual(remote.saves[0].lists[0].symbols, ['FPT']);
});
