import { useEffect, useRef } from 'react';
import {
    IChartApi,
    ISeriesApi,
    CandlestickSeries,
    HistogramSeries,
    LineSeries,
    createSeriesMarkers
} from 'lightweight-charts';
import {
    formatChartTime,
    formatReportDateForChart,
    formatIndicatorData
} from '../../../lib/services/timeseries';
import { Report } from '../../../lib/services/report';

type PricePanelProps = {
    chart: IChartApi;
    data: any; // Timeseries data
    indicators: any; // Indicators data
    reports: Report[];
    isChartReady: boolean;
    onSeriesReady: (series: ISeriesApi<"Candlestick">) => void;
};

export default function PricePanel({
    chart,
    data,
    indicators,
    reports,
    isChartReady,
    onSeriesReady
}: PricePanelProps) {
    const candlestickSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
    const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
    const markerSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
    const atrTrailingRef = useRef<ISeriesApi<"Line"> | null>(null);
    const vwapHighestRef = useRef<ISeriesApi<"Line"> | null>(null);
    const vwapLowestRef = useRef<ISeriesApi<"Line"> | null>(null);
    const markersRef = useRef<any>(null);

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

        return () => {
            if (chart) {
                try {
                    chart.removeSeries(candlestickSeries);
                    chart.removeSeries(volumeSeries);
                    chart.removeSeries(markerSeries);
                    chart.removeSeries(atrTrailing);
                    chart.removeSeries(vwapHighest);
                    chart.removeSeries(vwapLowest);
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
            markersRef.current = null;
        };
    }, [chart]);

    // Update data
    useEffect(() => {
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

        // Only update markers if chart is ready
        if (isChartReady) {
            // Set data for marker series (invisible line at bottom for marker positioning)
            const markerSeriesData = timestamps.map((timestamp: string) => ({
                time: formatChartTime(timestamp),
                value: 0,
            }));
            markerSeriesRef.current?.setData(markerSeriesData);

            // Create markers for reports
            const markers = reports
                .filter(report => report.ngaykn)
                .map(report => ({
                    time: formatReportDateForChart(report.ngaykn || ''),
                    position: 'inBar' as const,
                    color: '#2196F3',
                    text: '📄',
                    shape: '' as const,
                    size: 1,
                    title: `${report.tenbaocao}\n${report.nguon}\n${new Date(report.ngaykn || '').toLocaleDateString('en-GB', { timeZone: 'Asia/Ho_Chi_Minh' })}`,
                }));

            if (markers.length > 0 && markerSeriesRef.current) {
                markersRef.current = createSeriesMarkers(markerSeriesRef.current, markers as any);
            }
        }
    }, [data, indicators, reports, isChartReady]);

    return null;
}
