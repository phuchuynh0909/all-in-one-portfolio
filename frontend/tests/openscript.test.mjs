import assert from 'node:assert/strict';
import { readFile, mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import test, { after } from 'node:test';
import { build } from 'esbuild';

// Exercise the actual browser adapter, including its raw .os imports, in Node.
const result = await build({
  stdin: {
    contents: 'export * from "./src/lib/tv/openscript"; export * from "./src/lib/tv/scriptCompiler"; export * from "./src/lib/tv/userScripts"; export * from "./src/lib/services/openscripts"; export * from "./src/lib/services/chartLayouts";',
    resolveDir: process.cwd(), loader: 'ts',
  },
  bundle: true, write: false, format: 'esm', platform: 'node',
  define: { 'import.meta.env': '{}' },
  plugins: [{ name: 'raw-openscript', setup(builder) {
    builder.onResolve({ filter: /\.os\?raw$/ }, (args) => ({ path: new URL(args.path.replace('?raw', ''), `file://${args.resolveDir}/`).pathname, namespace: 'raw' }));
    builder.onLoad({ filter: /.*/, namespace: 'raw' }, async (args) => ({ contents: await readFile(args.path, 'utf8'), loader: 'text' }));
  } }],
});
const directory = await mkdtemp(join(tmpdir(), 'openscript-test-'));
after(() => rm(directory, { recursive: true, force: true }));
const bundle = join(directory, 'adapter.mjs');
await writeFile(bundle, result.outputFiles[0].text);
const { validateUserScript, buildOpenScriptStudy, readUserScripts, SCRIPT_TEMPLATE, USER_SCRIPTS_KEY, userScriptSpec, listOpenScripts, saveOpenScript, updateOpenScriptVisibility } = await import(pathToFileURL(bundle).href);
const { listChartLayouts, saveChartLayout, selectChartLayout, importChartLayouts, renameChartLayout, deleteChartLayout } = await import(pathToFileURL(bundle).href);

const pine = { Std: Object.fromEntries(['time', 'open', 'high', 'low', 'close', 'volume'].map((key) => [key, (context) => context[key]])) };
const bar = (index, close) => ({ time: 1_700_000_000_000 + index * 86_400_000, open: close - 1, high: close + 2, low: close - 2, close, volume: 1000 });
function studyFor(source) {
  const definition = buildOpenScriptStudy(pine, { id: 'user_test', name: 'Test', source, inputsFrom: () => ({}) });
  const study = new definition.constructor();
  const defaults = Object.values(definition.metainfo.defaults.inputs);
  const inputs = (index) => defaults[index];
  study.init({}, inputs);
  return { definition, study, inputs };
}

test('template compiles as a price overlay with declared inputs', () => {
  const { title, overlay, program } = validateUserScript(SCRIPT_TEMPLATE, 'user_test');
  assert.equal(title, 'My moving average');
  assert.equal(overlay, true);
  assert.equal(program.inputs[0].key, 'length');
});

test('invalid syntax includes a source location and does not produce a study', () => {
  assert.throws(() => validateUserScript('version 1\nstudy("Oops")\nplot(missing)', 'user_test'), /user_test\.os[\s\S]*3 \|/);
});

test('unsupported features fail explicitly before applying a chart change', () => {
  assert.throws(() => validateUserScript('version 1\nstrategy("Trade")\nplot(close, "Close")', 'user_test'), /Strategies/);
  assert.throws(() => validateUserScript('version 1\nstudy("Paint")\nplot(close, "Close")\nbarColor(red)', 'user_test'), /barColor/);
  assert.throws(() => validateUserScript('version 1\nstudy("Empty")', 'user_test'), /at least one/);
});

test('SMA survives placeholder context, live updates, inputs and a history restart', () => {
  const { study, inputs } = studyFor('version 1\nstudy("Average")\nlength = input(2, "Length")\nplot(sma(close, length), "SMA", orange)');
  assert.ok(Number.isNaN(study.main({}, inputs)[0]));
  assert.ok(Number.isNaN(study.main(bar(0, 10), inputs)[0]));
  assert.equal(study.main(bar(1, 20), inputs)[0], 15);
  assert.equal(study.main(bar(1, 30), inputs)[0], 20);
  assert.equal(study.main(bar(2, 40), inputs)[0], 35);
  assert.ok(Number.isNaN(study.main(bar(0, 50), inputs)[0]));
  assert.equal(study.main(bar(1, 70), inputs)[0], 60);
  assert.equal(study.main(bar(2, 90), () => 1)[0], 90);
});

test('boolean and string inputs use native settings types; levels and signals render', () => {
  const { definition, study, inputs } = studyFor('version 1\nstudy("Signals", overlay = true)\nshow = input(true, "Show")\nlabel = input("Example", "Label")\nplot(show ? close : none, "Close", orange)\nlevel(70, "High", red)\nif show\n    signal("BUY", lime, at = "below")');
  assert.deepEqual(definition.metainfo.inputs.map((input) => input.type), ['bool', 'text']);
  const values = study.main(bar(0, 100), inputs);
  assert.equal(values[0], 100);
  assert.equal(values[1], 70);
  assert.equal(values[2], 98);
});

test('saved scripts round-trip while keeping unapplied edits separate', () => {
  const appliedSource = SCRIPT_TEMPLATE;
  const source = SCRIPT_TEMPLATE.replace('My moving average', 'Renamed average');
  const original = [{ id: 'user_test', source, appliedSource, visible: true }];
  const scripts = readUserScripts({ getItem: (key) => key === USER_SCRIPTS_KEY ? JSON.stringify(original) : null });
  assert.equal(scripts[0].title, 'Renamed average');
  assert.equal(userScriptSpec(scripts[0]).source, appliedSource);
  assert.throws(() => readUserScripts({ getItem: () => '{broken' }));
  assert.throws(() => readUserScripts({ getItem: () => JSON.stringify([...original, ...original]) }), /Invalid saved script/);
});

test('server library loads sources and preserves the applied version', async (context) => {
  const source = SCRIPT_TEMPLATE.replace('My moving average', 'Server average');
  const stored = { id: 'user_server', title: 'Server average', source, applied_source: SCRIPT_TEMPLATE, visible: true };
  const fetch = context.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify([stored]), { status: 200 }));
  const scripts = await listOpenScripts();
  assert.equal(scripts[0].source, source);
  assert.equal(userScriptSpec(scripts[0]).source, SCRIPT_TEMPLATE);
  assert.match(fetch.mock.calls[0].arguments[0], /\/api\/v1\/openscripts$/);
});

test('saving waits for server confirmation and visibility changes send no source', async (context) => {
  const script = { id: 'user_server', title: 'My moving average', source: SCRIPT_TEMPLATE, appliedSource: SCRIPT_TEMPLATE, visible: true };
  const calls = [];
  context.mock.method(globalThis, 'fetch', async (url, init) => {
    calls.push({ url, ...init });
    return init.method === 'PATCH' ? new Response(null, { status: 204 })
      : new Response(JSON.stringify({ ...JSON.parse(init.body), id: script.id }), { status: 200 });
  });
  assert.equal((await saveOpenScript(script)).source, script.source);
  assert.equal(calls[0].method, 'PUT');
  assert.equal(JSON.parse(calls[0].body).applied_source, script.source);
  await updateOpenScriptVisibility([{ id: script.id, visible: false }]);
  assert.deepEqual(JSON.parse(calls[1].body), [{ id: script.id, visible: false }]);
});

test('server save failures are surfaced without reporting a local save', async (context) => {
  context.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify({ detail: 'Storage unavailable' }), { status: 503 }));
  await assert.rejects(saveOpenScript({ id: 'user_server', title: 'Example', source: SCRIPT_TEMPLATE, visible: false }), /Storage unavailable/);
});

test('server layout library restores membership, parameters and native indicator settings', async (context) => {
  const snapshot = { charts: [{ panes: [{ sources: [{ state: { inputs: { length: 55 } } }] }] }] };
  const layout = { id: 'layout1', name: 'Layout 1', indicators: ['rsi'], params: { rsi: { period: 21 } }, scripts: ['user_server'], chart_state: snapshot };
  context.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify({ layouts: [layout], active_layout_id: 'layout1' }), { status: 200 }));
  const library = await listChartLayouts();
  assert.equal(library.activeLayoutId, 'layout1');
  assert.deepEqual(library.layouts[0].chartState, snapshot);
  assert.deepEqual(library.layouts[0].scripts, ['user_server']);
  assert.equal(library.layouts[0].params.rsi.period, 21);
});

test('layout saves and selection send the full setup to the backend', async (context) => {
  const layout = { id: 'custom_test', name: 'Research', indicators: ['rsi'], params: { rsi: { period: 30 } }, scripts: ['user_server'], chartState: { panes: ['saved'] } };
  const stored = { ...layout, chart_state: layout.chartState };
  const calls = [];
  context.mock.method(globalThis, 'fetch', async (url, init) => {
    calls.push({ url, ...init });
    return new Response(JSON.stringify(url.endsWith('/import') ? { layouts: [stored], active_layout_id: layout.id } : stored), { status: 200 });
  });
  assert.deepEqual((await saveChartLayout(layout)).chartState, layout.chartState);
  assert.equal(calls[0].method, 'PUT');
  assert.deepEqual(JSON.parse(calls[0].body).chart_state, layout.chartState);
  await selectChartLayout(layout.id);
  assert.deepEqual(JSON.parse(calls[1].body), { layout_id: layout.id });
  assert.equal((await importChartLayouts([layout], layout.id)).activeLayoutId, layout.id);
  assert.equal(JSON.parse(calls[2].body).layouts[0].id, layout.id);
});

test('failed layout saves remain errors rather than falling back to browser storage', async (context) => {
  context.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify({ detail: 'Database unavailable' }), { status: 503 }));
  await assert.rejects(saveChartLayout({ id: 'custom_test', name: 'Example', indicators: [] }), /Database unavailable/);
});

test('renaming preserves chart state and deletion returns the selected fallback', async (context) => {
  const snapshot = { charts: [{ panes: [{ height: 240 }] }] };
  const renamed = { id: 'custom_test', name: 'Momentum', indicators: ['rsi'], params: { rsi: { period: 30 } }, scripts: ['user_server'], chart_state: snapshot };
  const fallback = { ...renamed, id: 'layout1', name: 'Layout 1' };
  const calls = [];
  context.mock.method(globalThis, 'fetch', async (url, init) => {
    calls.push({ url, ...init });
    return new Response(JSON.stringify(init.method === 'PATCH' ? renamed : { layouts: [fallback], active_layout_id: fallback.id }), { status: 200 });
  });
  assert.deepEqual((await renameChartLayout('custom_test', 'Momentum')).chartState, snapshot);
  assert.equal(calls[0].method, 'PATCH');
  assert.deepEqual(JSON.parse(calls[0].body), { name: 'Momentum' });
  const library = await deleteChartLayout('custom_test');
  assert.equal(calls[1].method, 'DELETE');
  assert.equal(calls[1].url, calls[0].url);
  assert.equal(library.activeLayoutId, 'layout1');
  assert.deepEqual(library.layouts.map((item) => item.id), ['layout1']);
});

test('failed rename and delete surface the server error', async (context) => {
  context.mock.method(globalThis, 'fetch', async (_url, init) => new Response(JSON.stringify({ detail: init.method === 'PATCH' ? 'A layout with this name already exists' : 'Layout not found' }), { status: init.method === 'PATCH' ? 409 : 404 }));
  await assert.rejects(renameChartLayout('custom_test', 'Momentum'), /already exists/);
  await assert.rejects(deleteChartLayout('custom_test'), /Layout not found/);
});
