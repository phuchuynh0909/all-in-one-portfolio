import { useMemo } from 'react';
import { useTheme } from '@mui/material/styles';
import { fontFamily, fontSize } from './tokens';

/**
 * Chart styling derived from the active theme.
 *
 * Every chart library in this app (lightweight-charts, recharts, MUI x-charts,
 * the TradingView charting library) needs concrete colour values rather than
 * CSS variables, and all of them must flip with the colour mode. This hook is
 * the single adapter — components should never hardcode chart colours.
 */
export function useChartTheme() {
  const theme = useTheme();

  return useMemo(() => {
    const { palette } = theme;
    const series = palette.chart.series;

    /** Colour for a signed value: green up, red down, neutral flat. */
    const pnlColor = (value: number | null | undefined): string => {
      if (value == null || Number.isNaN(value) || value === 0) return palette.market.flat;
      return value > 0 ? palette.market.long : palette.market.short;
    };

    /** Nth categorical series colour, wrapping around the palette. */
    const seriesColor = (index: number): string => series[index % series.length];

    return {
      mode: palette.mode,
      background: palette.surface.default,
      insetBackground: palette.surface.inset,
      text: palette.text.primary,
      textMuted: palette.text.secondary,
      grid: palette.chart.grid,
      axis: palette.chart.axis,
      border: palette.line.subtle,
      accent: palette.primary.main,
      up: palette.market.long,
      down: palette.market.short,
      flat: palette.market.flat,
      series,
      seriesColor,
      pnlColor,

      /** Spread into `createChart(el, { ...lightweightChartOptions })`. */
      lightweightChartOptions: {
        layout: {
          background: { color: 'transparent' },
          textColor: palette.text.secondary,
          fontFamily: fontFamily.mono,
          fontSize: 11,
        },
        grid: {
          vertLines: { color: palette.chart.grid },
          horzLines: { color: palette.chart.grid },
        },
        crosshair: {
          vertLine: {
            color: palette.primary.main,
            width: 1 as const,
            style: 3,
            labelBackgroundColor: palette.primary.main,
          },
          horzLine: {
            color: palette.primary.main,
            width: 1 as const,
            style: 3,
            labelBackgroundColor: palette.primary.main,
          },
        },
        rightPriceScale: { borderColor: palette.line.default },
        timeScale: { borderColor: palette.line.default },
      },

      /** Candlestick series colours. */
      candlestick: {
        upColor: palette.market.long,
        downColor: palette.market.short,
        borderUpColor: palette.market.long,
        borderDownColor: palette.market.short,
        wickUpColor: palette.market.long,
        wickDownColor: palette.market.short,
      },

      /** Common recharts axis/grid/tooltip props. */
      recharts: {
        grid: { stroke: palette.chart.grid, strokeDasharray: '3 3' },
        axis: {
          stroke: palette.chart.axis,
          tick: { fill: palette.text.secondary, fontSize: 11, fontFamily: fontFamily.mono },
          tickLine: false,
          axisLine: { stroke: palette.line.default },
        },
        tooltip: {
          contentStyle: {
            background: palette.surface.overlay,
            border: `1px solid ${palette.line.default}`,
            borderRadius: 4,
            fontSize: fontSize.xs,
            color: palette.text.primary,
            boxShadow: theme.shadows[3],
          },
          labelStyle: { color: palette.text.secondary, fontSize: fontSize['2xs'] },
          itemStyle: { color: palette.text.primary, fontSize: fontSize.xs },
          cursor: { fill: palette.action.hover },
        },
        legend: { wrapperStyle: { fontSize: fontSize.xs, color: palette.text.secondary } },
      },

      /** MUI x-charts `sx` overrides — x-charts does not read our theme. */
      xChartsSx: {
        '& .MuiChartsAxis-line': { stroke: palette.line.default },
        '& .MuiChartsAxis-tick': { stroke: palette.line.default },
        '& .MuiChartsAxis-tickLabel': {
          fill: palette.text.secondary,
          fontSize: 11,
          fontFamily: fontFamily.mono,
        },
        '& .MuiChartsAxis-label': { fill: palette.text.primary, fontSize: 12 },
        '& .MuiChartsGrid-line': { stroke: palette.chart.grid },
        '& .MuiChartsLegend-series text': { fill: `${palette.text.secondary} !important`, fontSize: '11px !important' },
      },
    };
  }, [theme]);
}

export default useChartTheme;
