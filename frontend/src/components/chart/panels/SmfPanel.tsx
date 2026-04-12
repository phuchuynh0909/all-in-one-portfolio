/**
 * Smart Money Flow Cloud panel.
 *
 * Replicates the BOSWaves TradingView indicator:
 *   • Basis cloud  — CandlestickSeries body between b_open and b_close (regime-colored)
 *   • Band fill    — CandlestickSeries body between (lower↔close) bull or (close↔upper) bear
 *   • Upper / lower band boundary lines
 *   • Buy / Sell labels at regime switches (arrowUp / arrowDown markers)
 *   • Retest dots (circle markers) at bull_dot / bear_dot bars
 *
 * All series live on pane 0 (price pane) as overlays.
 * CandlestickSeries with transparent wicks/borders gives us the "fill" primitive
 * that lightweight-charts lacks natively.
 */

import { useEffect, useRef } from 'react';
import {
  CandlestickSeries,
  LineSeries,
  createSeriesMarkers,
} from 'lightweight-charts';
import type { IChartApi, ISeriesApi, SeriesMarker, UTCTimestamp } from 'lightweight-charts';
import { formatIndicatorData } from '../../../lib/services/timeseries';

const BULL = '#00C8FF';
const BEAR = '#FF005D';
const TRANSPARENT = 'rgba(0,0,0,0)';

type CandlePoint = { time: UTCTimestamp; open: number; high: number; low: number; close: number };

type SmfPanelProps = {
  chart: IChartApi;
  data: any;
  timestamps: string[];
  visible?: boolean;
  /** Actual close prices from timeseriesData — used for accurate fill boundaries */
  closePrice?: (number | null | undefined)[];
};

export default function SmfPanel({
  chart,
  data,
  timestamps,
  visible,
  closePrice,
}: SmfPanelProps) {
  const bandFillRef    = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const basisCloudRef  = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const upperRef       = useRef<ISeriesApi<'Line'> | null>(null);
  const lowerRef       = useRef<ISeriesApi<'Line'> | null>(null);
  const markerSeriesRef= useRef<ISeriesApi<'Line'> | null>(null);
  const markersCtrlRef = useRef<{ setMarkers: (m: SeriesMarker<UTCTimestamp>[]) => void } | null>(null);

  // ── Create series ──────────────────────────────────────────────────────────
  useEffect(() => {
    if (!chart) return;

    // Band fill: regime-colored "candle body" spanning price ↔ band
    const bandFill = chart.addSeries(CandlestickSeries, {
      upColor:         'rgba(0,200,255,0.38)',
      downColor:       'rgba(255,0,93,0.38)',
      borderUpColor:   TRANSPARENT,
      borderDownColor: TRANSPARENT,
      wickUpColor:     TRANSPARENT,
      wickDownColor:   TRANSPARENT,
      priceLineVisible: false,
      lastValueVisible: false,
    }, 0);
    bandFillRef.current = bandFill;

    // Basis cloud: semi-transparent candle body between b_open and b_close
    const basisCloud = chart.addSeries(CandlestickSeries, {
      upColor:         'rgba(0,200,255,0.22)',
      downColor:       'rgba(255,0,93,0.22)',
      borderUpColor:   'rgba(0,200,255,0.35)',
      borderDownColor: 'rgba(255,0,93,0.35)',
      wickUpColor:     TRANSPARENT,
      wickDownColor:   TRANSPARENT,
      priceLineVisible: false,
      lastValueVisible: false,
    }, 0);
    basisCloudRef.current = basisCloud;

    // Upper band boundary — thin bull-colored line
    const upper = chart.addSeries(LineSeries, {
      color: 'rgba(0,200,255,0.55)',
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    }, 0);
    upperRef.current = upper;

    // Lower band boundary — thin bear-colored line
    const lower = chart.addSeries(LineSeries, {
      color: 'rgba(255,0,93,0.55)',
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    }, 0);
    lowerRef.current = lower;

    // Hidden anchor series for markers
    const markerSeries = chart.addSeries(LineSeries, {
      color: 'transparent',
      lineWidth: 1,
      lineVisible: false,
      priceScaleId: 'right',
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    }, 0);
    markerSeriesRef.current = markerSeries;
    markersCtrlRef.current = createSeriesMarkers(markerSeries, []);

    return () => {
      markersCtrlRef.current = null;
      try {
        if (bandFill)    chart.removeSeries(bandFill);
        if (basisCloud)  chart.removeSeries(basisCloud);
        if (upper)       chart.removeSeries(upper);
        if (lower)       chart.removeSeries(lower);
        if (markerSeries) chart.removeSeries(markerSeries);
      } catch (e) {
        console.warn('Error removing SMF series:', e);
      }
      bandFillRef.current    = null;
      basisCloudRef.current  = null;
      upperRef.current       = null;
      lowerRef.current       = null;
      markerSeriesRef.current= null;
    };
  }, [chart]);

  // ── Toggle visibility ──────────────────────────────────────────────────────
  useEffect(() => {
    const show = visible !== false;
    bandFillRef.current?.applyOptions({ visible: show });
    basisCloudRef.current?.applyOptions({ visible: show });
    upperRef.current?.applyOptions({ visible: show });
    lowerRef.current?.applyOptions({ visible: show });
    markerSeriesRef.current?.applyOptions({ visible: show });
    if (!show) markersCtrlRef.current?.setMarkers([]);
  }, [visible]);

  // ── Update data ────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!data || !timestamps) return;
    const smf = data.smart_money_flow;
    if (!smf) return;

    // Band boundaries
    upperRef.current?.setData(formatIndicatorData(timestamps, smf.upper ?? []));
    lowerRef.current?.setData(formatIndicatorData(timestamps, smf.lower ?? []));

    const bandFillData:   CandlePoint[] = [];
    const basisCloudData: CandlePoint[] = [];
    const anchorData:     { time: UTCTimestamp; value: number }[] = [];
    const markers:        SeriesMarker<UTCTimestamp>[] = [];

    timestamps.forEach((ts: string, i: number) => {
      const t     = (new Date(ts).getTime() / 1000) as UTCTimestamp;
      const sig   = smf.last_signal?.[i];
      const upper = smf.upper?.[i] as number | null | undefined;
      const lower = smf.lower?.[i] as number | null | undefined;
      const bC    = smf.b_close?.[i] as number | null | undefined;
      const bO    = smf.b_open?.[i]  as number | null | undefined;
      // Use actual close if provided, fall back to b_close
      const price = (closePrice?.[i] != null ? closePrice[i] : bC) as number | null | undefined;

      // ── Band fill ──────────────────────────────────────────────────────────
      if (price != null) {
        if (sig === 1 && lower != null) {
          // Bull: green body from lower → price  (close > open → upColor)
          const hi = Math.max(lower, price);
          const lo = Math.min(lower, price);
          bandFillData.push({ time: t, open: lower, close: price, high: hi, low: lo });
        } else if (sig === -1 && upper != null) {
          // Bear: red body from price → upper   (open > close → downColor)
          const hi = Math.max(upper, price);
          const lo = Math.min(upper, price);
          bandFillData.push({ time: t, open: upper, close: price, high: hi, low: lo });
        }
      }

      // ── Basis cloud ────────────────────────────────────────────────────────
      if (bO != null && bC != null) {
        const hi = Math.max(bO, bC);
        const lo = Math.min(bO, bC);
        basisCloudData.push({ time: t, open: bO, close: bC, high: hi, low: lo });
      }

      // ── Anchor for markers ─────────────────────────────────────────────────
      if (bC != null) anchorData.push({ time: t, value: bC });

      // ── Buy / Sell labels ─────────────────────────────────────────────────
      if (smf.switch_up?.[i]) {
        markers.push({
          time: t,
          position: 'belowBar',
          color: BULL,
          shape: 'arrowUp',
          text: 'Buy',
          size: 1,
        });
      }
      if (smf.switch_down?.[i]) {
        markers.push({
          time: t,
          position: 'aboveBar',
          color: BEAR,
          shape: 'arrowDown',
          text: 'Sell',
          size: 1,
        });
      }

      // ── Retest dots ────────────────────────────────────────────────────────
      if (smf.bull_dot?.[i]) {
        markers.push({ time: t, position: 'belowBar', color: BULL, shape: 'circle', text: '', size: 1 });
      }
      if (smf.bear_dot?.[i]) {
        markers.push({ time: t, position: 'aboveBar', color: BEAR, shape: 'circle', text: '', size: 1 });
      }
    });

    bandFillRef.current?.setData(bandFillData);
    basisCloudRef.current?.setData(basisCloudData);
    markerSeriesRef.current?.setData(anchorData);

    // lightweight-charts requires markers sorted by time
    markers.sort((a, b) => (a.time as number) - (b.time as number));
    markersCtrlRef.current?.setMarkers(markers);
  }, [data, timestamps, closePrice]);

  return null;
}
