import { useEffect, useRef } from 'react';
import {
    CandlestickSeries,
    HistogramSeries,
    LineSeries,
    createSeriesMarkers
} from 'lightweight-charts';
import type { IChartApi, ISeriesApi, SeriesMarker, UTCTimestamp } from 'lightweight-charts';
import {
    formatChartTime,
    formatReportDateForChart,
    formatIndicatorData
} from '../../../lib/services/timeseries';
import type { Report } from '../../../lib/services/report';

type PricePanelProps = {
    chart: IChartApi;
    data: any; // Timeseries data
    indicators: any; // Indicators data
    reports: Report[];
    positionMarkers?: PositionSeriesMarker[];
    isChartReady: boolean;
    onSeriesReady: (series: ISeriesApi<"Candlestick">) => void;
    atrVisible?: boolean;
    vwapVisible?: boolean;
    kamaVisible?: boolean;
    chandelierVisible?: boolean;
    linregVisible?: boolean;
};

type ChartMarker = SeriesMarker<UTCTimestamp>;
type BarMarkerPosition = 'inBar' | 'aboveBar' | 'belowBar';
export type PositionSeriesMarker = Omit<SeriesMarker<UTCTimestamp>, 'id' | 'position'> & {
    id: string;
    position: BarMarkerPosition;
};

type MarkerController = {
    setMarkers: (markers: ChartMarker[]) => void;
};

export default function PricePanel({
    chart,
    data,
    indicators,
    reports,
    positionMarkers = [],
    isChartReady,
    onSeriesReady,
    atrVisible,
    vwapVisible,
    kamaVisible,
    chandelierVisible,
    linregVisible,
}: PricePanelProps) {
    const candlestickSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
    const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
    const markerSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
    const atrTrailingRef = useRef<ISeriesApi<"Line"> | null>(null);
    const vwapHighestRef = useRef<ISeriesApi<"Line"> | null>(null);
    const vwapLowestRef = useRef<ISeriesApi<"Line"> | null>(null);
    const kamaRef = useRef<ISeriesApi<"Line"> | null>(null);
    const chandelierLineRef = useRef<ISeriesApi<"Line"> | null>(null);
    const linregRegRef = useRef<ISeriesApi<"Line"> | null>(null);
    const linregPiUpperRef = useRef<ISeriesApi<"Line"> | null>(null);
    const linregPiLowerRef = useRef<ISeriesApi<"Line"> | null>(null);
    const reportMarkersRef = useRef<MarkerController | null>(null);
    const positionMarkersRef = useRef<MarkerController | null>(null);

    useEffect(() => {
        if (!chart) return;

        // Create the candlestick series
        const candlestickSeries = chart.addSeries(CandlestickSeries, {
            upColor: '#22c55e',
            downColor: '#ef4444',
            borderVisible: false,
            wickUpColor: '#22c55e',
            wickDownColor: '#ef4444',
        });
        candlestickSeriesRef.current = candlestickSeries;
        onSeriesReady(candlestickSeries);

        // Create the volume series
        const volumeSeries = chart.addSeries(HistogramSeries, {
            color: '#6366f1',
            priceFormat: {
                type: 'volume',
            },
            priceScaleId: 'volume',
        });
        volumeSeriesRef.current = volumeSeries;

        // Configure volume scale
        chart.priceScale('volume').applyOptions({
            scaleMargins: {
                top: 0.8,
                bottom: 0,
            },
        });

        // Create hidden marker series at the bottom of panel 0 for report markers
        const markerSeries = chart.addSeries(LineSeries, {
            color: 'transparent',
            lineWidth: 1,
            lineVisible: false,
            priceScaleId: 'markers',
            lastValueVisible: false,
            priceLineVisible: false,
            crosshairMarkerVisible: false,
        });
        markerSeriesRef.current = markerSeries;

        // Position marker scale at the absolute bottom of the chart
        chart.priceScale('markers').applyOptions({
            scaleMargins: {
                top: 0.85,
                bottom: 0,
            },
            visible: false,
        });

        // Create ATR Trailing Stop series
        const atrTrailing = chart.addSeries(LineSeries, {
            color: '#22c55e',
            lineWidth: 2,
            lineStyle: 2,
            title: 'Trailing Stop',
            priceFormat: {
                type: 'price',
            },
            priceLineVisible: false,
        });
        atrTrailingRef.current = atrTrailing;

        // Create VWAP series
        const vwapHighest = chart.addSeries(LineSeries, {
            color: '#3b82f6',  // Blue
            lineWidth: 2,
            title: 'VWAP High',
            priceFormat: {
                type: 'price',
            },
            priceLineVisible: false,
        });
        vwapHighestRef.current = vwapHighest;

        const vwapLowest = chart.addSeries(LineSeries, {
            color: '#f97316',  // Orange
            lineWidth: 2,
            title: 'VWAP Low',
            priceFormat: {
                type: 'price',
            },
            priceLineVisible: false,
        });
        vwapLowestRef.current = vwapLowest;

        // KAMA — Kaufman Adaptive Moving Average overlay
        const kama = chart.addSeries(LineSeries, {
            color: '#eab308',  // Amber
            lineWidth: 2,
            title: 'KAMA',
            priceFormat: {
                type: 'price',
            },
            priceLineVisible: false,
        });
        kamaRef.current = kama;

        // Single chandelier line — color switches per bar based on direction
        const chandelierLine = chart.addSeries(LineSeries, {
            color: '#089981',
            lineWidth: 2,
            lineStyle: 2, // dashed
            title: 'CE',
            priceFormat: { type: 'price' },
            priceLineVisible: false,
            lastValueVisible: false,
        });
        chandelierLineRef.current = chandelierLine;

        // Linear Regression Prediction Channel — regression line + PI bands (overlay)
        const linregReg = chart.addSeries(LineSeries, {
            color: '#38bdf8',  // sky blue
            lineWidth: 2,
            title: 'LR Reg',
            priceFormat: { type: 'price' },
            priceLineVisible: false,
            lastValueVisible: false,
        });
        linregRegRef.current = linregReg;

        const linregPiUpper = chart.addSeries(LineSeries, {
            color: '#f43f5e',  // crimson
            lineWidth: 1,
            lineStyle: 2,  // dashed
            title: 'LR PI Up',
            priceFormat: { type: 'price' },
            priceLineVisible: false,
            lastValueVisible: false,
        });
        linregPiUpperRef.current = linregPiUpper;

        const linregPiLower = chart.addSeries(LineSeries, {
            color: '#f43f5e',  // crimson
            lineWidth: 1,
            lineStyle: 2,  // dashed
            title: 'LR PI Low',
            priceFormat: { type: 'price' },
            priceLineVisible: false,
            lastValueVisible: false,
        });
        linregPiLowerRef.current = linregPiLower;

        return () => {
            if (chart) {
                try {
                    chart.removeSeries(candlestickSeries);
                    chart.removeSeries(volumeSeries);
                    chart.removeSeries(markerSeries);
                    chart.removeSeries(atrTrailing);
                    chart.removeSeries(vwapHighest);
                    chart.removeSeries(vwapLowest);
                    chart.removeSeries(kama);
                    chart.removeSeries(chandelierLine);
                    chart.removeSeries(linregReg);
                    chart.removeSeries(linregPiUpper);
                    chart.removeSeries(linregPiLower);
                } catch (e) {
                    console.warn('Error removing series:', e);
                }
            }
            candlestickSeriesRef.current = null;
            volumeSeriesRef.current = null;
            markerSeriesRef.current = null;
            atrTrailingRef.current = null;
            vwapHighestRef.current = null;
            vwapLowestRef.current = null;
            kamaRef.current = null;
            chandelierLineRef.current = null;
            linregRegRef.current = null;
            linregPiUpperRef.current = null;
            linregPiLowerRef.current = null;
            reportMarkersRef.current = null;
            positionMarkersRef.current = null;
        };
    }, [chart]);

    // Toggle ATR Trailing visibility
    useEffect(() => {
        atrTrailingRef.current?.applyOptions({ visible: atrVisible !== false });
    }, [atrVisible]);

    // Toggle VWAP visibility
    useEffect(() => {
        const show = vwapVisible !== false;
        vwapHighestRef.current?.applyOptions({ visible: show });
        vwapLowestRef.current?.applyOptions({ visible: show });
    }, [vwapVisible]);

    // Toggle KAMA visibility
    useEffect(() => {
        kamaRef.current?.applyOptions({ visible: kamaVisible !== false });
    }, [kamaVisible]);

    // Toggle Chandelier Exit visibility
    useEffect(() => {
        chandelierLineRef.current?.applyOptions({ visible: chandelierVisible !== false });
    }, [chandelierVisible]);

    // Toggle Linear Regression Prediction Channel visibility
    useEffect(() => {
        const show = linregVisible !== false;
        linregRegRef.current?.applyOptions({ visible: show });
        linregPiUpperRef.current?.applyOptions({ visible: show });
        linregPiLowerRef.current?.applyOptions({ visible: show });
    }, [linregVisible]);

    // Update data
    useEffect(() => {
        reportMarkersRef.current?.setMarkers([]);
        positionMarkersRef.current?.setMarkers([]);

        if (!data || !indicators) return;

        const timestamps = data.timestamps || [];
        const timeseries = data.timeseries || {};

        // Format data for the chart
        const candleData = timestamps.map((timestamp: string, i: number) => ({
            time: formatChartTime(timestamp),
            open: timeseries.open?.[i] ?? 0,
            high: timeseries.high?.[i] ?? 0,
            low: timeseries.low?.[i] ?? 0,
            close: timeseries.close?.[i] ?? 0,
        }));

        const volumeData = timestamps.map((timestamp: string, i: number) => ({
            time: formatChartTime(timestamp),
            value: timeseries.volume?.[i] ?? 0,
            color: (timeseries.close?.[i] ?? 0) >= (timeseries.open?.[i] ?? 0) ? 'rgba(34, 197, 94, 0.5)' : 'rgba(239, 68, 68, 0.5)'
        }));

        candlestickSeriesRef.current?.setData(candleData);
        volumeSeriesRef.current?.setData(volumeData);

        const atrTrailingData = formatIndicatorData(timestamps, indicators.atr_trailing ?? []);
        atrTrailingRef.current?.setData(atrTrailingData);

        const vwapHighestData = formatIndicatorData(timestamps, indicators.vwap_highest ?? []);
        const vwapLowestData = formatIndicatorData(timestamps, indicators.vwap_lowest ?? []);
        vwapHighestRef.current?.setData(vwapHighestData);
        vwapLowestRef.current?.setData(vwapLowestData);

        const kamaData = formatIndicatorData(timestamps, indicators.kama ?? []);
        kamaRef.current?.setData(kamaData);

        // Linear Regression Prediction Channel — regression line + PI bands
        const lrc = indicators.linreg_channel;
        linregRegRef.current?.setData(formatIndicatorData(timestamps, lrc?.reg ?? []));
        linregPiUpperRef.current?.setData(formatIndicatorData(timestamps, lrc?.pi_upper ?? []));
        linregPiLowerRef.current?.setData(formatIndicatorData(timestamps, lrc?.pi_lower ?? []));

        // Chandelier Exit — single dashed line, green (dir=1) or red (dir=-1) per bar
        const ceData = indicators.chandelier_exit;
        if (ceData?.value && ceData?.direction) {
            const ceLineData = (timestamps as string[])
                .map((ts: string, i: number) => {
                    const val = ceData.value[i];
                    if (val == null) return null;
                    return {
                        time: formatChartTime(ts),
                        value: val as number,
                        color: ceData.direction[i] === 1 ? '#00ffff' : '#f23645',
                    };
                })
                .filter((d): d is { time: UTCTimestamp; value: number; color: string } => d !== null);
            chandelierLineRef.current?.setData(ceLineData);
        }

        // Only update markers if chart is ready
        if (isChartReady) {
            // Set data for marker series (invisible line at bottom for marker positioning)
            const markerSeriesData = timestamps.map((timestamp: string) => ({
                time: formatChartTime(timestamp),
                value: 0,
            }));
            markerSeriesRef.current?.setData(markerSeriesData);

            const reportMarkers: ChartMarker[] = reports
                .filter(report => report.ngaykn)
                .map(report => ({
                    time: formatReportDateForChart(report.ngaykn || ''),
                    position: 'inBar' as const,
                    color: '#2196F3',
                    text: '📄',
                    shape: 'circle' as const,
                    size: 1,
                }));

            if (markerSeriesRef.current) {
                if (!reportMarkersRef.current) {
                    reportMarkersRef.current = createSeriesMarkers(markerSeriesRef.current, reportMarkers);
                } else {
                    reportMarkersRef.current.setMarkers(reportMarkers);
                }
            }

            if (candlestickSeriesRef.current) {
                if (!positionMarkersRef.current) {
                    positionMarkersRef.current = createSeriesMarkers(candlestickSeriesRef.current, positionMarkers);
                } else {
                    positionMarkersRef.current.setMarkers(positionMarkers);
                }
            }
        }
    }, [data, indicators, reports, positionMarkers, isChartReady]);

    return null;
}
