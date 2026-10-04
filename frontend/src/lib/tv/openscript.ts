import {
  DiagnosticBag,
  check,
  emit,
  load,
  parse,
  renderDiagnostics,
  sourceFile,
  type CompiledProgram,
  type Engine,
  type SourceFile,
  type HostBar,
} from 'openalgo-script';

import type {
  CustomIndicator,
  IContext,
  IPineStudyResult,
  LibraryPineStudy,
  PineJS,
  StudyInputValue,
  StudyMetaInfo,
} from './charting_library';

const PLOT_LINE = 0;
const PLOT_HISTOGRAM = 1;
const LINE_SOLID = 0;
const PLOT_LINE_WITH_MARKERS = 14;
const LINE_DASHED = 2;

interface CompiledStudy {
  file: SourceFile;
  program: CompiledProgram;
}
type InputCallback = <T extends StudyInputValue>(index: number) => T;


export interface OpenScriptStudySpec {
  id: string;
  name: string;
  dashed?: boolean;
  dashedPlots?: string[];
  hiddenPlots?: string[];
  barColors?: string[];
  plotColors?: Record<string, string[]>;
  markerAnchors?: string[];
  source: string;
  inputsFrom: (params: Record<string, number>) => Record<string, StudyInputValue>;
}

function compileStudy(spec: OpenScriptStudySpec): CompiledStudy {
  const file = sourceFile(`${spec.id}.os`, spec.source);
  const diagnostics = new DiagnosticBag();
  const checked = check(file, parse(file, diagnostics), diagnostics);
  const { program } = emit(file, checked, diagnostics);

  if (diagnostics.hasErrors || !program) {
    throw new Error(`Invalid OpenScript study ${spec.id}:\n${renderDiagnostics(file, diagnostics.ordered())}`);
  }

  return { file, program };
}


function colorCss(value: unknown, fallback = '#9ca3af'): string {
  if (Array.isArray(value) && value.length === 4) {
    const [r, g, b, a] = value;
    if ([r, g, b, a].every((part) => typeof part === 'number')) return `rgba(${r}, ${g}, ${b}, ${a})`;
  }
  if (value && typeof value === 'object') {
    const { r, g, b, a } = value as Record<string, unknown>;
    if ([r, g, b, a].every((part) => typeof part === 'number')) return `rgba(${r}, ${g}, ${b}, ${a})`;
  }
  return fallback;
}

function finiteOrNull(value: number): number | null {
  return Number.isFinite(value) ? value : null;
}


function settingsFor(
  program: CompiledProgram,
  inputCallback: InputCallback,
): Record<string, StudyInputValue> {
  return Object.fromEntries(program.inputs.map((input, index) => [input.key, inputCallback(index)]));
}

function loadEngine(
  study: CompiledStudy,
  settings: Record<string, StudyInputValue>,
): Engine | null {
  const loaded = load(study.program, { source: study.file, settings });
  if (loaded.ok) return loaded.engine;

  console.warn(
    `OpenScript refused ${study.program.meta.title}: ${loaded.diagnostic.code} ${loaded.diagnostic.message}`,
  );
  return null;
}

/**
 * Adapts an OpenScript study to TradingView's custom-indicator lifecycle.
 * TradingView supplies one bar at a time; OpenScript owns the stateful maths and
 * receives append/update calls as historical and live bars move through it.
 */
export function buildOpenScriptStudy(pine: PineJS, spec: OpenScriptStudySpec): CustomIndicator {
  const study = compileStudy(spec);
  const { program } = study;

  const barColors = spec.barColors ?? [];
  const hasBarColors = program.outputs.barColor != null && barColors.length > 0;
  const barPaletteColors = Object.fromEntries(barColors.map((color, index) => [
    index,
    { name: `Color ${index}` },
  ]));
  const barPaletteDefaults = Object.fromEntries(barColors.map((color, index) => [
    index,
    { color, width: 1, style: LINE_SOLID },
  ]));
  const plotColors = spec.plotColors ?? {};
  const dynamicColorPlots = program.outputs.plots.filter(
    (plot) => plot.colorChannel != null && (plotColors[String(plot.title)]?.length ?? 0) > 0,
  );
  const dynamicPlotPalettes = Object.fromEntries(dynamicColorPlots.map((plot, index) => {
    const colors = plotColors[String(plot.title)] ?? [];
    return [
      `plotPal${index}`,
      {
        colors: Object.fromEntries(colors.map((color, colorIndex) => [colorIndex, { name: `Color ${colorIndex}` }])),
        valToIndex: Object.fromEntries(colors.map((_, colorIndex) => [colorIndex, colorIndex])),
        addDefaultColor: false,
      },
    ];
  }));
  const dynamicPlotPaletteDefaults = Object.fromEntries(dynamicColorPlots.map((plot, index) => {
    const colors = plotColors[String(plot.title)] ?? [];
    return [
      `plotPal${index}`,
      { colors: Object.fromEntries(colors.map((color, colorIndex) => [colorIndex, { color, width: 1, style: LINE_SOLID }])) },
    ];
  }));
  const markerAnchors = spec.markerAnchors ?? [];
  const hiddenPlots = spec.hiddenPlots ?? [];
  const markerAnchorChannels = program.outputs.markers.map((_, index) => {
    const title = markerAnchors[index];
    return program.outputs.plots.find((plot) => plot.title === title)?.channel ?? null;
  });
  const markerStyles = Object.fromEntries(program.outputs.markers.map((marker) => [
    marker.key,
    { title: marker.key, isHidden: false, size: 'tiny' },
  ]));
  const defaultMarkerStyles = Object.fromEntries(program.outputs.markers.map((marker) => [
    marker.key,
    {
      plottype: marker.shape === 'circle'
        ? 'shape_circle'
        : marker.shape === 'arrowUp'
          ? 'shape_arrow_up'
          : marker.shape === 'arrowDown'
            ? 'shape_arrow_down'
            : 'shape_label_up',
      location: marker.position === 'above' ? 'AboveBar' : marker.position === 'below' ? 'BelowBar' : 'Absolute',
      color: colorCss(marker.color),
      textColor: colorCss(marker.textColor ?? marker.color),
      transparency: 0,
      visible: true,
    },
  ]));
  const outputCount = program.outputs.plots.length + program.outputs.markers.length + dynamicColorPlots.length + (hasBarColors ? 1 : 0);
  const styles = Object.fromEntries([
    ...program.outputs.plots.map((plot) => [
      plot.key,
      { title: String(plot.title), histogramBase: 0, isHidden: hiddenPlots.includes(String(plot.title)) },
    ]),
    ...Object.entries(markerStyles),
  ]);
  const defaultStyles = Object.fromEntries(program.outputs.plots.map((plot) => [
    plot.key,
    {
      linestyle: spec.dashed || (spec.dashedPlots ?? []).includes(String(plot.title)) ? LINE_DASHED : LINE_SOLID,
      linewidth: typeof plot.width === 'number' ? plot.width : 1,
      plottype: plot.type === 'histogram' || plot.type === 'column'
        ? PLOT_HISTOGRAM
        : plot.type === 'lineWithMarkers'
          ? PLOT_LINE_WITH_MARKERS
          : PLOT_LINE,
      trackPrice: false,
      transparency: 0,
      visible: !hiddenPlots.includes(String(plot.title)),
      color: colorCss(plot.color),
    },
  ]));
  const filledAreas = program.outputs.fills.map((fill, index) => ({
    id: `fill${index}`,
    objAId: fill.between[0],
    objBId: fill.between[1],
    title: `Fill ${index + 1}`,
    type: 'plot_plot',
  }));
  const defaultFilledAreasStyle = Object.fromEntries(program.outputs.fills.map((fill, index) => [
    `fill${index}`,
    { color: colorCss(fill.colorUp), transparency: 0, visible: true },
  ]));
  const defaultInputs = Object.fromEntries(
    program.inputs.map((input) => [input.key, input.default[1]]),
  );

  const metainfoValue = {
    _metainfoVersion: 53,
    id: `${spec.id}@tv-custom-1`,
    name: spec.name,
    description: spec.name,
    shortDescription: spec.name,
    isCustomIndicator: true,
    is_price_study: Boolean(program.meta.overlay),
    format: program.meta.overlay
      ? { type: 'inherit' }
      : { type: 'price', precision: typeof program.meta.precision === 'number' ? program.meta.precision : 2 },
    plots: [
      ...program.outputs.plots.map((plot) => ({ id: plot.key, type: 'line' })),
      ...program.outputs.markers.map((marker) => ({ id: marker.key, type: 'shapes' })),
      ...dynamicColorPlots.map((plot, index) => ({
        id: `__plotColor${index}`, type: 'colorer', target: plot.key, palette: `plotPal${index}`,
      })),
      ...(hasBarColors ? [{ id: '__barColor', type: 'bar_colorer', palette: 'barPal' }] : []),
    ],
    inputs: program.inputs.map((input) => {
      const defaultValue = input.default[1];
      return {
        id: input.key,
        name: input.label,
        type: typeof defaultValue === 'number' && Number.isInteger(defaultValue) && (input.step ?? 1) >= 1
          ? 'integer'
          : 'float',
        defval: defaultValue,
        ...(input.min == null ? {} : { min: input.min }),
        ...(input.max == null ? {} : { max: input.max }),
        ...(input.step == null ? {} : { step: input.step }),
      };
    }),
    styles,
    filledAreas: filledAreas.length ? filledAreas : undefined,
    palettes: Object.keys(dynamicPlotPalettes).length || hasBarColors
      ? { ...dynamicPlotPalettes, ...(hasBarColors ? { barPal: { colors: barPaletteColors, valToIndex: Object.fromEntries(barColors.map((_, index) => [index, index])), addDefaultColor: false } } : {}) }
      : undefined,
    defaults: {
      styles: { ...defaultStyles, ...defaultMarkerStyles },
      palettes: Object.keys(dynamicPlotPaletteDefaults).length || hasBarColors
        ? { ...dynamicPlotPaletteDefaults, ...(hasBarColors ? { barPal: { colors: barPaletteDefaults } } : {}) }
        : undefined,
      precision: typeof program.meta.precision === 'number' ? program.meta.precision : 2,
      filledAreasStyle: filledAreas.length ? defaultFilledAreasStyle : undefined,
      inputs: defaultInputs,
    },
  };
  // TradingView's generated metadata type cannot express dynamically compiled
  // plot/input ids, although this object follows that runtime contract.
  const metainfo = metainfoValue as unknown as StudyMetaInfo;

  return {
    name: spec.name,
    metainfo,
    constructor: function (this: LibraryPineStudy<IPineStudyResult>) {
      let engine: Engine | null = null;
      let lastTime: number | null = null;
      let settingsKey = '';

      const reset = (inputCallback: InputCallback) => {
        const settings = settingsFor(program, inputCallback);
        settingsKey = JSON.stringify(settings);
        engine = loadEngine(study, settings);
        lastTime = null;
      };

      this.init = function (_context: IContext, inputCallback: InputCallback) {
        reset(inputCallback);
      };

      this.main = function (context: IContext, inputCallback: InputCallback) {
        const settings = settingsFor(program, inputCallback);
        const nextSettingsKey = JSON.stringify(settings);
        if (!engine || nextSettingsKey !== settingsKey) reset(inputCallback);
        if (!engine) return new Array(outputCount).fill(NaN);

        const time = pine.Std.time(context);
        const bar: HostBar = {
          time: finiteOrNull(time),
          open: finiteOrNull(pine.Std.open(context)),
          high: finiteOrNull(pine.Std.high(context)),
          low: finiteOrNull(pine.Std.low(context)),
          close: finiteOrNull(pine.Std.close(context)),
          volume: finiteOrNull(pine.Std.volume(context)),
        };

        // TradingView invokes custom studies once with an empty context before
        // the first real bar. Feeding that placeholder into OpenScript poisons
        // the engine permanently (OS6025: bar has no time), so wait until the
        // chart supplies a complete price bar.
        if (
          bar.time == null
          || bar.open == null
          || bar.high == null
          || bar.low == null
          || bar.close == null
        ) {
          return new Array(outputCount).fill(NaN);
        }

        // A backwards jump means TradingView started a fresh history pass on the
        // same study instance. Reload rather than appending invalid time order.
        if (lastTime != null && bar.time < lastTime) reset(inputCallback);
        if (!engine) return new Array(outputCount).fill(NaN);

        const result = lastTime === bar.time ? engine.update(bar) : engine.append(bar);
        lastTime = bar.time;
        if (result.diagnostic) {
          console.warn(
            `OpenScript stopped ${spec.name}: ${result.diagnostic.code} ${result.diagnostic.message}`,
          );
          return new Array(outputCount).fill(NaN);
        }

        const outputs = program.outputs.plots.map((plot) => {
          const value = result.columns[plot.channel];
          return typeof value === 'number' && Number.isFinite(value) ? value : NaN;
        });
        program.outputs.markers.forEach((marker, index) => {
          const anchor = markerAnchorChannels[index];
          const value = anchor == null ? NaN : result.columns[anchor];
          outputs.push(
            typeof result.columns[marker.channel] === 'string' && typeof value === 'number' && Number.isFinite(value)
              ? value
              : NaN,
          );
        });
        dynamicColorPlots.forEach((plot) => {
          const colors = plotColors[String(plot.title)] ?? [];
          const color = colorCss(result.columns[plot.colorChannel!], '');
          outputs.push(colors.indexOf(color));
        });
        if (hasBarColors && program.outputs.barColor) {
          const color = colorCss(result.columns[program.outputs.barColor.channel], '');
          outputs.push(barColors.indexOf(color));
        }
        return outputs;
      };
    },
  } as unknown as CustomIndicator;
}

export const ATR_TRAILING_OPENSCRIPT: OpenScriptStudySpec = {
  id: 'atr_trailing',
  name: 'ATR Trailing Stop',
  dashed: true,
  inputsFrom: (params) => ({
    atrLen: params.timeperiod ?? 10,
    multiplier: params.multiplier ?? 1.8,
  }),
  source: `version 1
study("ATR Trailing Stop", overlay = true, precision = 2)

atrLen = input(10, "ATR period", min = 2, max = 100, step = 1)
multiplier = input(1.8, "Multiplier", min = 0.5, max = 10, step = 0.1)

distance = atr(atrLen) * multiplier
var trail = none
previous = trail

if not isNone(distance)
    candidate = close > previous ? close - distance : close + distance
    if isNone(previous)
        trail = close + distance
    else if close > previous and close[1] > previous
        trail = max(previous, close - distance)
    else if close < previous and close[1] < previous
        trail = min(previous, close + distance)
    else
        trail = candidate

plot(trail, "Trailing Stop", green, width = 2)
`,
};

export const KAMA_OPENSCRIPT: OpenScriptStudySpec = {
  id: 'kama',
  name: 'KAMA',
  inputsFrom: (params) => ({
    length: params.timeperiod ?? 10,
  }),
  source: `version 1
study("KAMA", overlay = true, precision = 2)

length = input(10, "Period", min = 2, max = 200, step = 1)
fastConstant = 2.0 / 3.0
slowConstant = 2.0 / 31.0
direction = abs(change(close, length))
volatility = sum(abs(change(close)), length)
efficiency = volatility != 0 ? direction / volatility : 0
smoothing = pow(efficiency * (fastConstant - slowConstant) + slowConstant, 2)
var value = none

if not isNone(smoothing)
    value = isNone(value) ? close : value + smoothing * (close - value)

plot(value, "KAMA", rgb(251, 191, 36), width = 2)
`,
};

export const CHANDELIER_EXIT_OPENSCRIPT: OpenScriptStudySpec = {
  id: 'chandelier_exit',
  name: 'Chandelier Exit',
  plotColors: {
    'Chandelier Exit Overlay': ['rgba(8, 153, 129, 1)', 'rgba(242, 54, 69, 1)'],
  },
  markerAnchors: ['Chandelier Exit Overlay', 'Chandelier Exit Overlay'],
  inputsFrom: (params) => ({
    length: params.length ?? 22,
    multiplier: params.multiplier ?? 3,
  }),
  source: `version 1
study("Chandelier Exit", overlay = true, precision = 2)

length = input(22, "ATR length", min = 1, max = 100, step = 1)
multiplier = input(3, "ATR multiplier", min = 0, max = 10, step = 0.1)

rangeValue = bar.index == 0 ? high - low : max(high - low, max(abs(high - close[1]), abs(low - close[1])))
atrRangeTotal = sum(rangeValue, length + 1)
initialAtr = not isNone(atrRangeTotal) ? (atrRangeTotal - rangeValue[length]) / length : none
var atrValue = none
if not isNone(initialAtr)
    atrValue = isNone(atrValue) ? initialAtr : (atrValue * (length - 1) + rangeValue) / length
highestHigh = (highest(close, length) + highest(high, length)) / 2
lowestLow = (lowest(close, length) + lowest(low, length)) / 2
chandLong = highestHigh - atrValue * multiplier
chandShort = lowestLow + atrValue * multiplier

var direction = 1
previousDirection = direction
if not isNone(chandLong) and not isNone(chandShort)
    if close > chandShort
        direction = 1
    else if close < chandLong
        direction = -1

chandExit = direction == 1 ? chandLong : chandShort
trailPlot = plot(chandExit, "Chandelier Exit Overlay", direction == 1 ? #089981 : #F23645, width = 1)
bullExit = direction == 1 ? chandExit : none
bearExit = direction == -1 ? chandExit : none
bullBody = direction == 1 ? (high + low) / 2 : none
bearBody = direction == -1 ? (high + low) / 2 : none
bullExitPlot = plot(bullExit, "Bullish fill", fade(#089981, 100), width = 1)
bearExitPlot = plot(bearExit, "Bearish fill", fade(#F23645, 100), width = 1)
bullBodyPlot = plot(bullBody, "Bullish body", fade(#089981, 100), width = 1)
bearBodyPlot = plot(bearBody, "Bearish body", fade(#F23645, 100), width = 1)
fill(bullBodyPlot, bullExitPlot, fade(#089981, 81))
fill(bearExitPlot, bearBodyPlot, fade(#F23645, 81))
`,
};

export const BVC_OPENSCRIPT: OpenScriptStudySpec = {
  id: 'bvc',
  name: 'BVC',
  inputsFrom: (params) => ({
    window: params.window ?? 20,
    kappa: params.kappa ?? 0.1,
  }),
  source: `version 1
study("BVC", precision = 2)

window = input(20, "Window", min = 5, max = 200, step = 1)
kappa = input(0.1, "Kappa", min = 0.01, max = 1, step = 0.01)

returnValue = bar.index == 0 ? 0 : log(close / close[1])
sigma = stdev(returnValue[1], window)
decay = exp(-kappa)
var accumulator = 0
bvc = none
if bar.index >= window and not isNone(sigma)
    label = 0
    if sigma > 0
        z = returnValue / sigma
        absoluteZ = abs(z)
        upper = absoluteZ / (1 + absoluteZ)
        interval = upper / 128
        integral = 0
        for i = 0 to 128
            u = i * interval
            remaining = 1 - u
            t = u / remaining
            density = 0.2148518357574702 * pow(1 + t * t / 0.25, -0.625) / (remaining * remaining)
            weight = i == 0 or i == 128 ? 1 : mod(i, 2) == 0 ? 2 : 4
            integral += weight * density
        cdf = 0.5 + (z >= 0 ? 1 : -1) * integral * interval / 3
        label = 2 * cdf - 1
    accumulator = accumulator * decay + volume * label
    bvc = accumulator / 100000

plot(bvc, "BVC", purple, width = 2)
`,
};

export const GAUSSIAN_FRAMA_OPENSCRIPT: OpenScriptStudySpec = {
  id: 'gaussian_frama',
  name: 'Gaussian FRAMA',
  dashedPlots: ['G-FRAMA Long', 'G-FRAMA Short'],
  plotColors: {
    'G-FRAMA': ['rgba(59, 130, 246, 1)', 'rgba(239, 68, 68, 1)', 'rgba(138, 147, 163, 1)'],
  },
  inputsFrom: (params) => ({
    gaussianLength: params.gaussian_length ?? 4,
    sigma: params.sigma ?? 2,
    fmLength: params.fm_len ?? 20,
    upperLimit: params.upper_limit ?? 8,
    lowerLimit: params.lower_limit ?? 40,
    atrPeriod: params.atr_period ?? 14,
    atrMultiplier: params.atr_mult ?? 1.9,
  }),
  source: `version 1
study("Gaussian FRAMA", overlay = true, precision = 2)

gaussianLength = input(4, "Gaussian length", min = 2, max = 20, step = 1)
sigma = input(2, "Sigma", min = 0.5, max = 5, step = 0.1)
fmLength = input(20, "FRAMA length", min = 5, max = 60, step = 1)
upperLimit = input(8, "Upper limit", min = 2, max = 30, step = 1)
lowerLimit = input(40, "Lower limit", min = 10, max = 100, step = 1)
atrPeriod = input(14, "ATR period", min = 2, max = 50, step = 1)
atrMultiplier = input(1.9, "ATR multiplier", min = 0.5, max = 5, step = 0.1)

gaussian = none
if bar.index >= gaussianLength - 1
    gaussianNumerator = 0
    gaussianDenominator = 0
    for index = 0 to gaussianLength - 1
        weight = exp(-0.5 * pow((index - (gaussianLength - 1) / 2) / sigma, 2))
        gaussianNumerator += close[index] * weight
        gaussianDenominator += weight
    gaussian = gaussianNumerator / gaussianDenominator

halfLength = floor(fmLength / 2)
fullRange = (highest(high, fmLength) - lowest(low, fmLength)) / fmLength
recentRange = (highest(high, halfLength) - lowest(low, halfLength)) / halfLength
priorRange = (highest(high[halfLength], halfLength) - lowest(low[halfLength], halfLength)) / halfLength
var previousDimension = 0
var frama = none
if bar.index >= fmLength and not isNone(gaussian)
    dimension = recentRange > 0 and priorRange > 0 and fullRange > 0 ? (log(recentRange + priorRange) - log(fullRange)) / log(2) : previousDimension
    previousDimension = dimension
    decay = log(2 / (lowerLimit + 1))
    rawAlpha = min(max(exp(decay * (dimension - 1)), 0.01), 1)
    oldLength = (2 - rawAlpha) / rawAlpha
    newLength = (lowerLimit - upperLimit) * (oldLength - 1) / (lowerLimit - 1) + upperLimit
    newAlpha = min(max(2 / (newLength + 1), 2 / (lowerLimit + 1)), 1)
    frama = isNone(frama) ? gaussian : (1 - newAlpha) * frama + newAlpha * gaussian

rangeValue = bar.index == 0 ? high - low : max(high - low, max(abs(high - close[1]), abs(low - close[1])))
initialAtr = sma(rangeValue, atrPeriod)
var atrValue = none
if not isNone(initialAtr)
    atrValue = isNone(atrValue) ? initialAtr : (atrValue * (atrPeriod - 1) + rangeValue) / atrPeriod
longBand = frama + atrMultiplier * atrValue
shortBand = frama - atrValue
var regime = 0
if not isNone(longBand) and not isNone(shortBand)
    if close > longBand
        regime = 1
    else if close < shortBand
        regime = -1

framaPlot = plot(frama, "G-FRAMA", regime == 1 ? #3B82F6 : regime == -1 ? #EF4444 : #8A93A3, width = 2)
longPlot = plot(longBand, "G-FRAMA Long", #3B82F6, width = 1)
shortPlot = plot(shortBand, "G-FRAMA Short", #EF4444, width = 1)
fill(longPlot, shortPlot, fade(#8B5CF6, 88))
`,
};

export const LR_PREDICTION_OPENSCRIPT: OpenScriptStudySpec = {
  id: 'linreg_channel',
  name: 'LR Prediction Channel',
  dashedPlots: ['LR PI Up', 'LR PI Low'],
  inputsFrom: (params) => ({
    window: params.reg_window ?? 50,
    confidence: params.confidence ?? 0.85,
  }),
  source: `version 1
study("LR Prediction Channel", overlay = true, precision = 2)

window = input(50, "Regression window", min = 10, max = 200, step = 5)
confidence = input(0.85, "Confidence", min = 0.5, max = 0.99, step = 0.01)

p = 1 - (1 - confidence) / 2
z = 0
if p <= 0.97575
    q = p - 0.5
    r = q * q
    z = (((((-39.69683028665376 * r + 220.9460984245205) * r - 275.9285104469687) * r + 138.357751867269) * r - 30.66479806614716) * r + 2.506628277459239) * q / (((((-54.47609879822406 * r + 161.5858368580409) * r - 155.6989798598866) * r + 66.80131188771972) * r - 13.28068155288572) * r + 1)
else
    q = sqrt(-2 * log(1 - p))
    tailNumerator = ((((-0.007784894002430293 * q - 0.3223964580411365) * q - 2.400758277161838) * q - 2.549732539343734) * q + 4.374664141464968) * q + 2.938163982698783
    tailDenominator = ((((0.007784695709041462 * q + 0.3224671290700398) * q + 2.445134137142996) * q + 3.754408661907416) * q + 1)
    z = -tailNumerator / tailDenominator
degrees = window - 2
tCritical = z + (pow(z, 3) + z) / (4 * degrees) + (5 * pow(z, 5) + 16 * pow(z, 3) + 3 * z) / (96 * pow(degrees, 2)) + (3 * pow(z, 7) + 19 * pow(z, 5) + 17 * pow(z, 3) - 15 * z) / (384 * pow(degrees, 3)) + (79 * pow(z, 9) + 779 * pow(z, 7) + 1482 * pow(z, 5) - 1920 * pow(z, 3) - 945 * z) / (92160 * pow(degrees, 4))

meanX = (window - 1) / 2
sxx = window * (window * window - 1) / 12
leverage = meanX * meanX / sxx
regression = none
predictionUpper = none
predictionLower = none
if bar.index >= window - 1
    sumY = 0
    sumXY = 0
    for k = 0 to window - 1
        value = close[window - 1 - k]
        sumY += value
        sumXY += (k - meanX) * value
    meanY = sumY / window
    slope = sumXY / sxx
    residualSum = 0
    for k = 0 to window - 1
        value = close[window - 1 - k]
        fitted = meanY + slope * (k - meanX)
        residual = value - fitted
        residualSum += residual * residual
    residualStdError = sqrt(residualSum / degrees)
    regression = meanY + slope * meanX
    predictionHalfWidth = tCritical * residualStdError * sqrt(1 + 1 / window + leverage)
    predictionUpper = regression + predictionHalfWidth
    predictionLower = regression - predictionHalfWidth

regressionPlot = plot(regression, "LR Reg", #3B82F6, width = 1)
upperPlot = plot(predictionUpper, "LR PI Up", #60A5FA, width = 1)
lowerPlot = plot(predictionLower, "LR PI Low", #60A5FA, width = 1)
fill(upperPlot, lowerPlot, fade(#8B5CF6, 88))
`,
};

export const HULL_BUTTERFLY_OPENSCRIPT: OpenScriptStudySpec = {
  id: 'hull_butterfly',
  name: 'Hull Butterfly Oscillator',
  inputsFrom: (params) => ({
    length: params.length ?? 14,
    mult: params.mult ?? 2.0,
  }),
  source: `version 1
study("Hull Butterfly Oscillator", precision = 2)

length = input(14, "Length", min = 4, max = 50, step = 1)
mult = input(2.0, "Multiplier", min = 0.5, max = 5, step = 0.1)

var coeffs: array<number> = []

if size(coeffs) == 0
    halfLength = floor(length / 2)
    hullLength = floor(sqrt(length))
    shortDen = halfLength * (halfLength + 1) / 2
    longDen = length * (length + 1) / 2
    hullDen = hullLength * (hullLength + 1) / 2
    var lcwa: array<number> = []
    for i = 0 to length - 1
        shortWeight = max(halfLength - i, 0)
        longWeight = length - i
        unshift(lcwa, 2 * shortWeight / shortDen - longWeight / longDen)
    for i = 1 to hullLength - 1
        unshift(lcwa, 0)
    for i = hullLength to size(lcwa) - 1
        value = 0
        for j = i - hullLength to i - 1
            value += element(lcwa, j) * (i - j)
        unshift(coeffs, value / hullDen)

window = size(coeffs) - 1
hso = none
if bar.index >= window
    forward = 0
    inverse = 0
    for i = 0 to window
        coefficient = element(coeffs, i)
        forward += close[i] * coefficient
        inverse += close[window - i] * coefficient
    hso = forward - inverse

var absoluteSum = 0
var samples = 0
var previousHso = none
var previousMean = none
var signalState = 0

if not isNone(hso)
    absoluteSum += abs(hso)
    samples += 1
    mean = absoluteSum / samples * mult
    crossed = not isNone(previousHso) and not isNone(previousMean) and ((previousHso < previousMean and hso > mean) or (previousHso > previousMean and hso < mean) or (previousHso < -previousMean and hso > -mean) or (previousHso > -previousMean and hso < -mean))
    if crossed
        signalState = 0
    else if hso < previousHso and hso > mean
        signalState = -1
    else if hso > previousHso and hso < -mean
        signalState = 1
    previousHso = hso
    previousMean = mean

plot(signalState == 1 ? hso : none, "Bullish", lime, style = "histogram", width = 2)
plot(signalState == -1 ? hso : none, "Bearish", red, style = "histogram", width = 2)
plot(signalState == 0 ? hso : none, "Neutral", gray, style = "histogram", width = 2)
`,
};

export const KALMAN_ZSCORE_OPENSCRIPT: OpenScriptStudySpec = {
  id: 'kalman_zscore',
  name: 'Kalman Z-Score',
  inputsFrom: (params) => ({
    window: params.window ?? 20,
  }),
  source: `version 1
study("Kalman Z-Score", precision = 2)

window = input(20, "Window", min = 5, max = 200, step = 1)

var stateMean = 0
var stateCovariance = 1
predictedCovariance = bar.index == 0 ? stateCovariance : stateCovariance + 0.01
gain = predictedCovariance / (predictedCovariance + 1)
stateMean = stateMean + gain * (close - stateMean)
stateCovariance = (1 - gain) * predictedCovariance

rollingMean = sma(stateMean, window)
rollingDeviation = stdev(stateMean, window, true)
zscore = not isNone(rollingMean) and not isNone(rollingDeviation) and rollingDeviation != 0 ? (stateMean - rollingMean) / rollingDeviation : 0

level(2, "Overbought", red)
level(-2, "Oversold", lime)
level(0, "Zero", gray)
plot(zscore, "Kalman Z-Score", aqua, width = 2)
`,
};

export const YZ_VOLATILITY_OPENSCRIPT: OpenScriptStudySpec = {
  id: 'yz_volatility',
  name: 'YZ Volatility',
  inputsFrom: (params) => ({
    window: params.window ?? 30,
    periods: params.periods ?? 252,
  }),
  source: `version 1
study("YZ Volatility", precision = 4)

window = input(30, "Window", min = 5, max = 200, step = 1)
periods = input(252, "Annual periods", min = 52, max = 365, step = 1)

k = 0.34 / (1.34 + (window + 1) / (window - 1))
overnight = log(open / close[1])
intraday = log(close / open)
range = log(high / low)

overnightVariance = sma(overnight * overnight, window)
intradayVariance = sma(intraday * intraday, window)
rogersSatchell = 0.5 * range * range - (2 * log(2) - 1) * (intraday * intraday + overnight * overnight)
rogersSatchellVariance = max(sma(rogersSatchell, window), 0)
yangZhangVariance = max(overnightVariance + k * intradayVariance + (1 - k) * rogersSatchellVariance, 0)
volatility = sqrt(yangZhangVariance) * sqrt(periods)

plot(volatility, "YZ Volatility", fuchsia, width = 2)
`,
};

export const WILLIAMS_VIX_FIX_OPENSCRIPT: OpenScriptStudySpec = {
  id: 'williams_vix_fix',
  name: 'Williams VIX Fix',
  inputsFrom: () => ({
    period: 22,
    multiplier: 2.0,
    bandLength: 20,
    rangeLength: 50,
    percentileHigh: 0.85,
    longLookback: 40,
    mediumLookback: 14,
    strength: 1,
  }),
  source: `version 1
study("Williams VIX Fix", precision = 2)

period = input(22, "WVF period", min = 2, max = 200, step = 1)
multiplier = input(2.0, "Band multiplier", min = 0.1, max = 10, step = 0.1)
bandLength = input(20, "Band length", min = 2, max = 200, step = 1)
rangeLength = input(50, "Range length", min = 2, max = 500, step = 1)
percentileHigh = input(0.85, "Percentile high", min = 0.1, max = 1, step = 0.01)
longLookback = input(40, "Long lookback", min = 2, max = 500, step = 1)
mediumLookback = input(14, "Medium lookback", min = 2, max = 500, step = 1)
strength = input(1, "Strength", min = 1, max = 50, step = 1)

highestClose = highest(close, period)
wvf = (highestClose - low) / highestClose * 100
midline = sma(wvf, bandLength)
upperBand = midline + stdev(wvf, bandLength) * multiplier
rangeHigh = highest(wvf, rangeLength) * percentileHigh

upRange = low > low[1] and close > close[1]
filtered = (wvf[1] >= upperBand[1] or wvf[1] >= rangeHigh[1]) and wvf < upperBand and wvf < rangeHigh
condFe = upRange and close > close[strength] and (close < close[longLookback] or close < close[mediumLookback]) and filtered
isFiltered = filtered == true
isCondFe = condFe == true

plot(isFiltered ? wvf : none, "Filtered", green, style = "histogram", width = 2)
plot(not isFiltered ? wvf : none, "WVF", maroon, style = "histogram", width = 2)
plot(isCondFe ? 1 : none, "FE", blue, style = "lineWithMarkers", width = 2)
`,
};

export const GKYZ_VOLATILITY_OPENSCRIPT: OpenScriptStudySpec = {
  id: 'gkyz_volatility',
  name: 'GKYZ Volatility',
  inputsFrom: (params) => ({
    window: params.window ?? 21,
  }),
  source: `version 1
study("GKYZ Volatility", precision = 3)

window = input(21, "Window", min = 5, max = 200, step = 1)

roundedOpen = round(open, 2)
roundedHigh = round(high, 2)
roundedLow = round(low, 2)
roundedClose = round(close, 2)
previousClose = bar.index == 0 ? roundedClose : roundedClose[1]

gap = log(roundedOpen / previousClose)
range = log(roundedHigh / roundedLow)
drift = log(roundedClose / roundedOpen)
gkyzVariance = sma(gap * gap, window) + 0.5 * sma(range * range, window) - (2 * log(2) - 1) * sma(drift * drift, window)
raw = sqrt(max(gkyzVariance, 0)) * sqrt(252) * 100
rollingLow = lowest(raw, window)
rollingHigh = highest(raw, window)
normalized = (raw - rollingLow) / (rollingHigh - rollingLow + 0.0000000001)

plot(normalized, "GKYZ", orange, width = 2)
plot(0.8, "High", red, style = "line", width = 1)
plot(0.2, "Low", lime, style = "line", width = 1)
`,
};

export const SQUEEZE_TTM_OPENSCRIPT: OpenScriptStudySpec = {
  id: 'squeeze_ttm',
  name: 'Squeeze TTM',
  inputsFrom: () => ({
    bbLength: 22,
    bbMultiplier: 2.0,
    kcLength: 16,
    kcMultiplier: 0.8,
    atrLength: 13,
    donchianLength: 11,
    smoothingLength: 8,
  }),
  source: `version 1
study("Squeeze TTM", precision = 2)

bbLength = input(22, "Bollinger length", min = 2, max = 200, step = 1)
bbMultiplier = input(2.0, "Bollinger multiplier", min = 0.1, max = 10, step = 0.1)
kcLength = input(16, "Keltner length", min = 2, max = 200, step = 1)
kcMultiplier = input(0.8, "Keltner multiplier", min = 0.1, max = 10, step = 0.1)
atrLength = input(13, "ATR length", min = 2, max = 200, step = 1)
donchianLength = input(11, "Donchian length", min = 2, max = 200, step = 1)
smoothingLength = input(8, "Smoothing length", min = 2, max = 200, step = 1)

bbUpper = sma(close, bbLength) + stdev(close, bbLength) * bbMultiplier
rangeValue = max(high - low, max(abs(high - close[1]), abs(low - close[1])))
initialAtr = sma(rangeValue, atrLength)
var atrValue = none
if not isNone(initialAtr)
    atrValue = isNone(atrValue) ? initialAtr : (atrValue * (atrLength - 1) + rangeValue) / atrLength
kcUpper = ema(close, kcLength) + atrValue * kcMultiplier
diff = bbUpper - kcUpper
histogram = close - ((highest(high, donchianLength) + lowest(low, donchianLength)) / 2 + sma(close, donchianLength)) / 2
momentum = linreg(histogram, smoothingLength)
isOn = (diff < 0) == true
isOff = (diff > 0) == true

plot(isOn ? momentum : none, "Squeeze On", maroon, style = "histogram", width = 2)
plot(isOff ? momentum : none, "Squeeze Off", lime, style = "histogram", width = 2)
plot(not isOn and not isOff ? momentum : none, "Neutral", gray, style = "histogram", width = 2)
`,
};

export const VWAP_BANDS_OPENSCRIPT: OpenScriptStudySpec = {
  id: 'vwap',
  name: 'VWAP Bands',
  inputsFrom: (params) => ({
    window: params.window ?? 200,
  }),
  source: `version 1
study("VWAP Bands", overlay = true, precision = 2)

window = input(200, "Window", min = 10, max = 1000, step = 10)

highVwap = none
lowVwap = none
if bar.index >= window - 1
    highAnchor = 0
    lowAnchor = 0
    highestClose = close[0]
    lowestClose = close[0]
    for offset = 0 to window - 1
        price = close[offset]
        if price >= highestClose
            highestClose = price
            highAnchor = offset
        if price <= lowestClose
            lowestClose = price
            lowAnchor = offset
    highWeighted = 0
    highVolume = 0
    for offset = 0 to highAnchor
        barVolume = volume[offset]
        if not isNone(barVolume)
            typicalPrice = (high[offset] + low[offset] + close[offset]) / 3
            highWeighted += typicalPrice * barVolume
            highVolume += barVolume
    lowWeighted = 0
    lowVolume = 0
    for offset = 0 to lowAnchor
        barVolume = volume[offset]
        if not isNone(barVolume)
            typicalPrice = (high[offset] + low[offset] + close[offset]) / 3
            lowWeighted += typicalPrice * barVolume
            lowVolume += barVolume
    highVwap = highVolume > 0 ? highWeighted / highVolume : none
    lowVwap = lowVolume > 0 ? lowWeighted / lowVolume : none

plot(highVwap, "VWAP High", aqua, width = 2)
plot(lowVwap, "VWAP Low", orange, width = 2)
`,
};

export const MATRIX_SERIES_OPENSCRIPT: OpenScriptStudySpec = {
  id: 'matrix_series',
  name: 'Matrix Series',
  markerAnchors: ['MS Up', 'MS Down'],
  inputsFrom: (params) => ({
    pricePeriod: params.price_period ?? 20,
    supResPeriod: params.sup_res_period ?? 50,
    supResPercentage: params.sup_res_percentage ?? 100,
    smoother: params.smoother ?? 5,
  }),
  source: `version 1
study("Matrix Series", precision = 1)

pricePeriod = input(20, "Price period", min = 5, max = 200, step = 1)
supResPeriod = input(50, "S/R period", min = 10, max = 500, step = 5)
supResPercentage = input(100, "S/R percent", min = 10, max = 500, step = 10)
smoother = input(5, "Smoother", min = 1, max = 50, step = 1)

valueCci = cci(pricePeriod)
valueMax = highest(valueCci, supResPeriod)
valueMin = lowest(valueCci, supResPeriod)
valueRange = (valueMax - valueMin) * supResPercentage / 100
resistance = valueMin + valueRange
support = valueMax - valueRange

weightedPrice = (high + low + close * 2) / 4
basis = ema(weightedPrice, smoother)
deviation = stdev(weightedPrice, smoother)
safeDeviation = deviation == 0 ? 1 : deviation
normalized = (weightedPrice - basis) * 200 / safeDeviation
firstSmooth = ema(normalized, smoother)
up = ema(firstSmooth, smoother)
down = ema(up, smoother)
rangeHigh = up < down ? up : down
rangeLow = up < down ? down : up

supportPlot = plot(support, "MS Support", red, width = 2)
resistancePlot = plot(resistance, "MS Resistance", lime, width = 2)
plot(up, "MS Up", aqua, width = 1)
plot(down, "MS Down", yellow, width = 1)
highPlot = plot(rangeHigh, "MS High", lime, width = 1)
lowPlot = plot(rangeLow, "MS Low", red, width = 1)
if up > 200
    signal("Up signal", orange, at = "price", shape = "circle")
if down < -200
    signal("Down signal", orange, at = "price", shape = "circle")
fill(highPlot, lowPlot, fade(aqua, 80))
`,
};

export const SMART_MONEY_FLOW_OPENSCRIPT: OpenScriptStudySpec = {
  id: 'smart_money_flow',
  name: 'SMF Cloud',
  barColors: ['rgba(0, 200, 255, 1)', 'rgba(255, 0, 93, 1)'],
  // hiddenPlots: ['Bullish basis open', 'Bullish basis close', 'Bearish basis open', 'Bearish basis close'],
  inputsFrom: (params) => ({
    trendLength: params.trend_len ?? 34,
    basisType: params.basis_type ?? 1,
    almaOffset: params.alma_offset ?? 0.85,
    almaSigma: params.alma_sigma ?? 6,
    basisSmooth: params.basis_smooth ?? 3,
    mfLength: params.mf_len ?? 24,
    mfSmooth: params.mf_smooth ?? 5,
    mfPower: params.mf_power ?? 1.2,
    atrLength: params.atr_len ?? 14,
    minMultiplier: params.min_mult ?? 0.9,
    maxMultiplier: params.max_mult ?? 2.2,
    dotCooldown: params.dot_cooldown ?? 12,
  }),
  source: `version 1
study("SMF Cloud", overlay = true)

trendLength = input(34, "Trend length", min = 5, max = 200, step = 1)
basisType = input(1, "Basis type", min = 0, max = 1, step = 1)
almaOffset = input(0.85, "ALMA offset", min = 0, max = 1, step = 0.01)
almaSigma = input(6, "ALMA sigma", min = 1, max = 20, step = 0.1)
basisSmooth = input(3, "Basis smooth", min = 1, max = 50, step = 1)
mfLength = input(24, "MF length", min = 5, max = 200, step = 1)
mfSmooth = input(5, "MF smooth", min = 1, max = 50, step = 1)
mfPower = input(1.2, "MF power", min = 0.1, max = 5, step = 0.1)
atrLength = input(14, "ATR length", min = 2, max = 100, step = 1)
minMultiplier = input(0.9, "Min multiplier", min = 0.1, max = 5, step = 0.1)
maxMultiplier = input(2.2, "Max multiplier", min = 0.5, max = 10, step = 0.1)
dotCooldown = input(12, "Dot cooldown", min = 0, max = 100, step = 1)


basisAlpha = 2 / (trendLength + 1)
smoothAlpha = 2 / (basisSmooth + 1)
mfAlpha = 2 / (mfSmooth + 1)

var emaOpen = none
var emaClose = none
emaOpen = isNone(emaOpen) ? open : emaOpen + basisAlpha * (open - emaOpen)
emaClose = isNone(emaClose) ? close : emaClose + basisAlpha * (close - emaClose)
almaOpen = alma(open, trendLength, almaOffset, almaSigma)
almaClose = alma(close, trendLength, almaOffset, almaSigma)
rawOpen = basisType == 1 ? almaOpen : emaOpen
rawClose = basisType == 1 ? almaClose : emaClose

var basisOpen = none
var basisClose = none
if not isNone(rawOpen)
    basisOpen = isNone(basisOpen) ? rawOpen : basisOpen + smoothAlpha * (rawOpen - basisOpen)
if not isNone(rawClose)
    basisClose = isNone(basisClose) ? rawClose : basisClose + smoothAlpha * (rawClose - basisClose)

priceRange = high - low
clv = priceRange != 0 ? ((close - low) - (high - close)) / priceRange : 0
rawFlow = clv * volume
flowNumerator = sum(rawFlow, mfLength)
flowDenominator = sum(abs(rawFlow), mfLength)
flowRaw = not isNone(flowDenominator) ? (flowDenominator != 0 ? flowNumerator / flowDenominator : 0) : none
var flowSmooth = none
if not isNone(flowRaw)
    flowSmooth = isNone(flowSmooth) ? flowRaw : flowSmooth + mfAlpha * (flowRaw - flowSmooth)
flowStrength = not isNone(flowSmooth) ? min(pow(abs(flowSmooth), mfPower), 1) : none
multiplier = minMultiplier + (maxMultiplier - minMultiplier) * flowStrength

rangeValue = bar.index == 0 ? high - low : max(high - low, max(abs(high - close[1]), abs(low - close[1])))
atrRangeTotal = sum(rangeValue, atrLength + 1)
initialAtr = not isNone(atrRangeTotal) ? (atrRangeTotal - rangeValue[atrLength]) / atrLength : none
var atrValue = none
if not isNone(initialAtr)
    atrValue = isNone(atrValue) ? initialAtr : (atrValue * (atrLength - 1) + rangeValue) / atrLength
upper = basisClose + atrValue * multiplier
lower = basisClose - atrValue * multiplier

previousUpper = upper[1]
previousLower = lower[1]
longCondition = not isNone(previousUpper) and close[1] <= previousUpper and close > upper
shortCondition = not isNone(previousLower) and close[1] >= previousLower and close < lower
var signalState = -1
if bar.index == 0 and not isNone(basisClose) and close >= basisClose
    signalState = 1
if longCondition
    signalState = 1
else if shortCondition
    signalState = -1

var lastBullDot = -100000
var lastBearDot = -100000
bullRetest = false
bearRetest = false
if signalState == 1 and low < basisClose and (dotCooldown == 0 or bar.index - lastBullDot >= dotCooldown)
    bullRetest = true
    lastBullDot = bar.index
if signalState == -1 and high > basisClose and (dotCooldown == 0 or bar.index - lastBearDot >= dotCooldown)
    bearRetest = true
    lastBearDot = bar.index


bearUpperPlot = plot(signalState == -1 ? upper : none, "Bearish upper", fade(#FF005D, 100), width = 1)
bearPricePlot = plot(signalState == -1 ? close : none, "Bearish price", fade(#FF005D, 100), width = 1)
bullLowerPlot = plot(signalState == 1 ? lower : none, "Bullish lower", fade(#00C8FF, 100), width = 1)
bullPricePlot = plot(signalState == 1 ? close : none, "Bullish price", fade(#00C8FF, 100), width = 1)
plot(bullRetest ? low : none, "Bullish retest", #00C8FF, style = "lineWithMarkers", width = 1)
plot(bearRetest ? high : none, "Bearish retest", #FF005D, style = "lineWithMarkers", width = 1)
fill(bearUpperPlot, bearPricePlot, fade(#FF005D, 70))
fill(bullLowerPlot, bullPricePlot, fade(#00C8FF, 70))
barColor(signalState == 1 ? #00C8FF : #FF005D)
`,
};

// fill(bullOpenPlot, bullClosePlot, fade(#00C8FF, 75))
// fill(bearOpenPlot, bearClosePlot, fade(#FF005D, 75))
// bullOpenPlot = plot(signalState == 1 ? basisOpen : none, "Bullish basis open", #00C8FF, width = 1)
// bullClosePlot = plot(signalState == 1 ? basisClose : none, "Bullish basis close", #00C8FF, width = 2)
// bearOpenPlot = plot(signalState == -1 ? basisOpen : none, "Bearish basis open", #FF005D, width = 1)
// bearClosePlot = plot(signalState == -1 ? basisClose : none, "Bearish basis close", #FF005D, width = 2)