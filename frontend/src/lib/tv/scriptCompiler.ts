import { DiagnosticBag, check, emit, load, parse, renderDiagnostics, sourceFile } from 'openalgo-script';

export function compileOpenScript(source: string, id: string) {
  const file = sourceFile(`${id}.os`, source);
  const diagnostics = new DiagnosticBag();
  const checked = check(file, parse(file, diagnostics), diagnostics);
  const { program } = emit(file, checked, diagnostics);
  if (diagnostics.hasErrors || !program) {
    throw new Error(renderDiagnostics(file, diagnostics.ordered()));
  }
  return { file, program };
}

/** Validate the chart adapter's capabilities before changing a working chart. */
export function validateUserScript(source: string, id: string) {
  if (source.length > 100_000) throw new Error('Scripts must be under 100,000 characters.');
  const compiled = compileOpenScript(source, id);
  const { program } = compiled;
  if (program.meta.kind !== 'study') throw new Error('Use study(...) to create an indicator. Strategies are not supported here.');
  if (program.requests.length) throw new Error('Requests for other symbols or timeframes are not supported yet.');
  if (program.inputs.some((input) => !['number', 'boolean', 'string'].includes(typeof input.default[1]) || ['source', 'time', 'color'].includes(input.kind))) {
    throw new Error('Custom indicator inputs currently support numbers, booleans and text.');
  }
  const { outputs } = program;
  if (outputs.tables.length || outputs.alerts.length || outputs.background) {
    throw new Error('Tables, alerts and background colors are not supported in custom chart indicators yet.');
  }
  if (outputs.barColor || outputs.plots.some((plot) => plot.colorChannel != null)) {
    throw new Error('Use a separate plot for each color. Dynamic plot colors and barColor are not supported in the editor yet.');
  }
  if (outputs.plots.some((plot) => !['line', 'histogram', 'column', 'lineWithMarkers'].includes(plot.type) || plot.offset !== 0)) {
    throw new Error('Use line, histogram, column or lineWithMarkers plots without an offset.');
  }
  if (outputs.markers.some((marker) => marker.position === 'price')) {
    throw new Error('Use at = "above" or at = "below" for signal markers.');
  }
  if (!outputs.plots.length && !outputs.levels.length && !outputs.markers.length) {
    throw new Error('Add at least one plot, level or signal to your indicator.');
  }
  const loaded = load(program, { source: compiled.file });
  if (!loaded.ok) throw new Error(`${loaded.diagnostic.code}: ${loaded.diagnostic.message}`);
  // A short execution pass catches runtime failures before the chart is reloaded.
  for (let index = 0; index < 3; index++) {
    const result = loaded.engine.append({ time: 1_700_000_000_000 + index * 86_400_000, open: 100, high: 102, low: 99, close: 101, volume: 1000 });
    if (result.diagnostic) throw new Error(`${result.diagnostic.code}: ${result.diagnostic.message}`);
  }
  return { ...compiled, title: String(program.meta.title), overlay: Boolean(program.meta.overlay) };
}
