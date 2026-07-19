import { useEffect } from 'react';
import type { RefObject } from 'react';
import type { IChartApi, ISeriesApi, UTCTimestamp } from 'lightweight-charts';
import { fetchLargeOrders, type LargeOrderDay } from '../../lib/services/largeOrders';

interface Props {
  chart: IChartApi;
  seriesRef: RefObject<ISeriesApi<'Candlestick'> | null>;
  container: HTMLDivElement;
  symbol: string;
  visible: boolean;
  minValue?: number;
  showSmall?: boolean;
  showMedium?: boolean;
  showBig?: boolean;
  showLabels?: boolean;
}

const BUY_COLOR = '#3b82f6';   // blue
const SELL_COLOR = '#ef4444';  // red
// Discrete tier radii (Pine: size.normal / size.large / size.huge).
const TIER_RADIUS: Record<1 | 2 | 3, number> = { 1: 6, 2: 10, 3: 14 };

function fmtNum(v: number): string {
  const a = Math.abs(v);
  const s = v < 0 ? '-' : '';
  if (a >= 1e9) return `${s}${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${s}${(a / 1e6).toFixed(2)}M`;
  if (a >= 1e3) return `${s}${(a / 1e3).toFixed(1)}K`;
  return `${s}${a.toFixed(0)}`;
}

function quantile(sorted: number[], q: number): number {
  if (sorted.length === 0) return 0;
  const i = (sorted.length - 1) * q;
  const lo = Math.floor(i);
  const hi = Math.ceil(i);
  return lo === hi ? sorted[lo] : sorted[lo] + (sorted[hi] - sorted[lo]) * (i - lo);
}

/**
 * Imperatively overlays one net large-order bubble per day on the daily candle
 * chart (Pine volume-cluster style): net delta decides the side, the bubble
 * sits at the day's HIGH (net-sell) or LOW (net-buy), size tier comes from the
 * day's total notional, and the label shows the day's large-order volume.
 */
export default function LargeOrderBubbles({
  chart, seriesRef, container, symbol, visible, minValue,
  showSmall = true, showMedium = true, showBig = true, showLabels = true,
}: Props) {
  useEffect(() => {
    if (!visible) return;

    let cancelled = false;
    let blocks: LargeOrderDay[] = [];
    let p50 = 0, p85 = 0, avgQty = 1;

    const canvas = document.createElement('canvas');
    canvas.style.cssText = 'position:absolute;inset:0;pointer-events:none;z-index:3;';
    container.appendChild(canvas);

    const tip = document.createElement('div');
    tip.style.cssText = [
      'position:absolute', 'z-index:4', 'pointer-events:none', 'display:none',
      'padding:7px 10px', 'border-radius:6px', 'font-size:11.5px', 'line-height:1.45',
      'font-family:"SF Mono","Fira Code",Monaco,monospace',
      'background:rgba(15,15,25,0.96)', 'border:1px solid rgba(99,102,241,0.4)',
      'color:#e5e7eb', 'white-space:pre',
    ].join(';');
    container.appendChild(tip);

    const tierOf = (b: LargeOrderDay): 1 | 2 | 3 =>
      b.total_value >= p85 ? 3 : b.total_value >= p50 ? 2 : 1;

    const tierVisible = (t: 1 | 2 | 3) =>
      t === 1 ? showSmall : t === 2 ? showMedium : showBig;

    // time -> {high, low}; plus the last candle for today-lag fallback.
    const candleAt = (t: number): { high: number; low: number } | null => {
      const data = seriesRef.current?.data() as
        | ReadonlyArray<{ time: number; high: number; low: number }>
        | undefined;
      if (!data || data.length === 0) return null;
      // binary-ish search not needed; daily counts are small
      for (let i = data.length - 1; i >= 0; i--) {
        if (data[i].time === t) return { high: data[i].high, low: data[i].low };
      }
      const last = data[data.length - 1];
      return t >= last.time ? { high: last.high, low: last.low } : null;
    };

    const coordX = (t: number): number | null => {
      let x = chart.timeScale().timeToCoordinate(t as UTCTimestamp);
      if (x == null) {
        const data = seriesRef.current?.data() as
          | ReadonlyArray<{ time: number }> | undefined;
        const last = data && data.length ? data[data.length - 1].time : null;
        if (last != null && t >= last) x = chart.timeScale().timeToCoordinate(last as UTCTimestamp);
      }
      return x;
    };

    // bubble center in pixels (anchored above HIGH for sell, below LOW for buy)
    const center = (b: LargeOrderDay, r: number): { x: number; y: number } | null => {
      const series = seriesRef.current;
      if (!series) return null;
      const x = coordX(b.time);
      const candle = candleAt(b.time);
      if (x == null || !candle) return null;
      const isSell = b.side === 2;
      const yAnchor = series.priceToCoordinate(isSell ? candle.high : candle.low);
      if (yAnchor == null) return null;
      return { x, y: isSell ? yAnchor - r - 3 : yAnchor + r + 3 };
    };

    const sync = () => {
      const dpr = window.devicePixelRatio || 1;
      canvas.width = container.clientWidth * dpr;
      canvas.height = container.clientHeight * dpr;
      canvas.style.width = `${container.clientWidth}px`;
      canvas.style.height = `${container.clientHeight}px`;
      const ctx = canvas.getContext('2d');
      if (ctx) ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    const draw = () => {
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      // timeToCoordinate / priceToCoordinate are pane-relative (origin to the
      // right of the left price axis). The canvas covers the whole container,
      // so shift drawing right by the left-axis width to land on the candles.
      const dpr = window.devicePixelRatio || 1;
      const leftW = chart.priceScale('left')?.width() ?? 0;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, container.clientWidth, container.clientHeight);
      ctx.translate(leftW, 0);
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.font = '10px "SF Mono", Monaco, monospace';
      for (const b of blocks) {
        const t = tierOf(b);
        if (!tierVisible(t)) continue;
        const r = TIER_RADIUS[t];
        const c = center(b, r);
        if (!c) continue;
        const color = b.side === 1 ? BUY_COLOR : SELL_COLOR;
        ctx.beginPath();
        ctx.arc(c.x, c.y, r, 0, Math.PI * 2);
        ctx.fillStyle = color + '4d';
        ctx.fill();
        ctx.lineWidth = 1.5;
        ctx.strokeStyle = color + 'dd';
        ctx.stroke();
        if (showLabels) {
          const label = fmtNum(b.total_qty);
          // label sits just outside the bubble (above sell, below buy)
          const ly = b.side === 2 ? c.y - r - 7 : c.y + r + 7;
          ctx.fillStyle = '#cbd5e1';
          ctx.fillText(label, c.x, ly);
        }
      }
    };

    const onCrosshair = (param: { point?: { x: number; y: number } }) => {
      const pt = param.point;
      if (!pt) { tip.style.display = 'none'; return; }
      const sorted = [...blocks].sort((a, b) => b.total_value - a.total_value);
      for (const b of sorted) {
        const t = tierOf(b);
        if (!tierVisible(t)) continue;
        const r = TIER_RADIUS[t];
        const c = center(b, r);
        if (!c) continue;
        if ((pt.x - c.x) ** 2 + (pt.y - c.y) ** 2 <= r * r) {
          const sd = b.side === 1 ? 'BUY' : 'SELL';
          const col = b.side === 1 ? BUY_COLOR : SELL_COLOR;
          const lvl = t === 3 ? 'BIG' : t === 2 ? 'MEDIUM' : 'SMALL';
          const ratio = avgQty > 0 ? (b.total_qty / avgQty).toFixed(2) : '–';
          tip.innerHTML =
            `<span style="color:${col};font-weight:700">── ${lvl} ${sd} ──</span>\n` +
            `Volume:  ${fmtNum(b.total_qty)} shares\n` +
            `Value:   ${fmtNum(b.total_value * 1000)}\n` +
            `Net Δ:   ${fmtNum(b.net_delta * 1000)}\n` +
            `Buy:     ${fmtNum(b.buy_value * 1000)}\n` +
            `Sell:    ${fmtNum(b.sell_value * 1000)}\n` +
            `Rel vol: ${ratio}x\n` +
            `<span style="color:#9ca3af">${b.date}`;
          tip.style.display = 'block';
          const leftW = chart.priceScale('left')?.width() ?? 0;
          const left = Math.min(pt.x + leftW + 14, container.clientWidth - 210);
          const top = Math.max(pt.y - 90, 6);
          tip.style.left = `${left}px`;
          tip.style.top = `${top}px`;
          return;
        }
      }
      tip.style.display = 'none';
    };

    chart.timeScale().subscribeVisibleTimeRangeChange(draw);
    chart.subscribeCrosshairMove(onCrosshair);
    const ro = new ResizeObserver(() => { sync(); draw(); });
    ro.observe(container);

    fetchLargeOrders(symbol, { minValue })
      .then((resp) => {
        if (cancelled) return;
        blocks = resp.blocks;
        const vals = blocks.map((b) => b.total_value).sort((a, b) => a - b);
        p50 = quantile(vals, 0.5);
        p85 = quantile(vals, 0.85);
        avgQty = blocks.length
          ? blocks.reduce((s, b) => s + b.total_qty, 0) / blocks.length
          : 1;
        sync();
        draw();
        requestAnimationFrame(draw);
        setTimeout(draw, 120);
      })
      .catch(() => { /* overlay is best-effort */ });

    return () => {
      cancelled = true;
      ro.disconnect();
      chart.timeScale().unsubscribeVisibleTimeRangeChange(draw);
      chart.unsubscribeCrosshairMove(onCrosshair);
      canvas.remove();
      tip.remove();
    };
  }, [chart, seriesRef, container, symbol, visible, minValue, showSmall, showMedium, showBig, showLabels]);

  return null;
}
