import { useEffect, useRef } from 'react';
import {
    IChartApi,
    ISeriesApi,
    CandlestickSeries,
    LineSeries,
    createSeriesMarkers
} from 'lightweight-charts';
import {
    formatChartTime,
    formatIndicatorData
} from '../../../lib/services/timeseries';

type MatrixSeriesPanelProps = {
    chart: IChartApi;
    data: any; // Indicators data
    timestamps: string[];
    visible?: boolean;
};

export default function MatrixSeriesPanel({ chart, data, timestamps, visible }: MatrixSeriesPanelProps) {
    const matrixSeriesCandleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
    const matrixSeriesSupportRef = useRef<ISeriesApi<"Line"> | null>(null);
    const matrixSeriesResistanceRef = useRef<ISeriesApi<"Line"> | null>(null);
    const matrixSeriesMarkerRef = useRef<ISeriesApi<"Line"> | null>(null);
    const matrixSeriesMarkersRef = useRef<any>(null);

    // Toggle series visibility
    useEffect(() => {
        const show = visible !== false;
        matrixSeriesCandleRef.current?.applyOptions({ visible: show });
        matrixSeriesSupportRef.current?.applyOptions({ visible: show });
        matrixSeriesResistanceRef.current?.applyOptions({ visible: show });
        matrixSeriesMarkerRef.current?.applyOptions({ visible: show });
    }, [visible]);

    useEffect(() => {
        if (!chart) return;

        // Create Matrix Series panel (Panel 5) - candlestick-like oscillator
        const matrixSeriesCandle = chart.addSeries(CandlestickSeries, {
            upColor: '#22c55e',
            downColor: '#ef4444',
            borderVisible: false,
            wickUpColor: '#22c55e',
            wickDownColor: '#ef4444',
            priceScaleId: 'right',
        }, 5);
        matrixSeriesCandle.moveToPane(4);
        matrixSeriesCandleRef.current = matrixSeriesCandle;

        // Matrix Series Support Line (red)
        const matrixSeriesSupport = chart.addSeries(LineSeries, {
            color: '#ef4444',  // Red
            lineWidth: 2,
            priceFormat: {
                type: 'custom',
                formatter: (price: number) => price.toFixed(1),
            },
            title: 'MS Support',
            priceScaleId: 'right',
            lastValueVisible: true,
        }, 5);
        matrixSeriesSupport.moveToPane(4);
        matrixSeriesSupportRef.current = matrixSeriesSupport;

        // Matrix Series Resistance Line (green)
        const matrixSeriesResistance = chart.addSeries(LineSeries, {
            color: '#22c55e',  // Green
            lineWidth: 2,
            priceFormat: {
                type: 'custom',
                formatter: (price: number) => price.toFixed(1),
            },
            title: 'MS Resistance',
            priceScaleId: 'right',
            lastValueVisible: true,
        }, 5);
        matrixSeriesResistance.moveToPane(4);
        matrixSeriesResistanceRef.current = matrixSeriesResistance;

        // Hidden line series for overbought/oversold markers in Matrix Series panel
        const matrixSeriesMarker = chart.addSeries(LineSeries, {
            color: 'transparent',
            lineWidth: 1,
            lineVisible: false,
            priceScaleId: 'right',
            lastValueVisible: false,
            priceLineVisible: false,
            crosshairMarkerVisible: false,
        }, 5);
        matrixSeriesMarker.moveToPane(4);
        matrixSeriesMarkerRef.current = matrixSeriesMarker;

        return () => {
            if (chart) {
                try {
                    chart.removeSeries(matrixSeriesCandle);
                    chart.removeSeries(matrixSeriesSupport);
                    chart.removeSeries(matrixSeriesResistance);
                    chart.removeSeries(matrixSeriesMarker);
                } catch (e) {
                    console.warn('Error removing series:', e);
                }
            }
            matrixSeriesCandleRef.current = null;
            matrixSeriesSupportRef.current = null;
            matrixSeriesResistanceRef.current = null;
            matrixSeriesMarkerRef.current = null;
            matrixSeriesMarkersRef.current = null;
        };
    }, [chart]);

    // Update data
    useEffect(() => {
        // Clear existing markers first
        if (matrixSeriesMarkersRef.current) {
            matrixSeriesMarkersRef.current = matrixSeriesMarkersRef.current.setMarkers([]);
        }

        if (!data || !timestamps || !data.matrix_series) return;

        const msHh = data.matrix_series.hh ?? [];
        const msLl = data.matrix_series.ll ?? [];
        const msSupportLine = data.matrix_series.support_line ?? [];
        const msResistanceLine = data.matrix_series.resistance_line ?? [];
        const msUpLine = data.matrix_series.up_line ?? [];
        const msDownLine = data.matrix_series.down_line ?? [];

        // Create candlestick data from hh/ll (validate to avoid "Value is null" in lightweight-charts)
        const rawMatrixCandleData = timestamps.map((timestamp: string, i: number) => {
            const hh = msHh[i];
            const ll = msLl[i];
            if (hh == null || ll == null || typeof hh !== 'number' || typeof ll !== 'number' || isNaN(hh) || isNaN(ll)) return null;

            const time = formatChartTime(timestamp);
            if (typeof time !== 'number' || isNaN(time) || !isFinite(time)) return null;

            // Determine color based on direction (compare to previous)
            const prevHh = i > 0 ? msHh[i - 1] : hh;
            const prevLl = i > 0 ? msLl[i - 1] : ll;
            const currentMid = (hh + ll) / 2;
            const prevMid = ((prevHh ?? hh) + (prevLl ?? ll)) / 2;
            const isUp = currentMid >= prevMid;

            return {
                time,
                open: hh,
                high: Math.max(hh, ll),
                low: Math.min(hh, ll),
                close: ll,
                color: isUp ? '#22c55e' : '#ef4444',
                borderColor: isUp ? '#22c55e' : '#ef4444',
                wickColor: isUp ? '#22c55e' : '#ef4444',
            };
        }).filter((c): c is NonNullable<typeof c> => c != null);

        const matrixCandleData = rawMatrixCandleData
            .sort((a, b) => a.time - b.time)
            .reduce((acc: typeof rawMatrixCandleData, c) => {
                const last = acc[acc.length - 1];
                if (!last || last.time !== c.time) acc.push(c);
                else acc[acc.length - 1] = c;
                return acc;
            }, []);

        if (matrixCandleData.length > 0) {
            matrixSeriesCandleRef.current?.setData(matrixCandleData);
        }

        // Support line
        const supportData = formatIndicatorData(timestamps, msSupportLine);
        matrixSeriesSupportRef.current?.setData(supportData);

        // Resistance line
        const resistanceData = formatIndicatorData(timestamps, msResistanceLine);
        matrixSeriesResistanceRef.current?.setData(resistanceData);

        // Set marker series data (use ll values for positioning)
        const markerSeriesData = timestamps.map((timestamp: string, i: number) => ({
            time: formatChartTime(timestamp),
            value: msLl[i] ?? 0,
        })).filter(d => d.value !== 0);
        matrixSeriesMarkerRef.current?.setData(markerSeriesData);

        // Create overbought/oversold markers
        const OB_LEVEL = 200;
        const OS_LEVEL = -200;

        const msMarkers: any[] = [];
        timestamps.forEach((timestamp: string, i: number) => {
            const up = msUpLine[i];
            const down = msDownLine[i];

            if (up == null || down == null) return;

            // Overbought: up > ob
            if (up > down && up > OB_LEVEL) {
                msMarkers.push({
                    time: formatChartTime(timestamp),
                    position: 'aboveBar' as const,
                    color: '#00bcd4',  // Cyan/aqua
                    shape: 'circle' as const,
                    text: '',
                    size: 0.5,
                });
            }

            // Oversold: down < os
            if (up < down && down < OS_LEVEL) {
                msMarkers.push({
                    time: formatChartTime(timestamp),
                    position: 'belowBar' as const,
                    color: '#00bcd4',  // Cyan/aqua
                    shape: 'circle' as const,
                    text: '',
                    size: 0.5,
                });
            }
        });

        // Apply markers
        if (msMarkers.length > 0 && matrixSeriesMarkerRef.current) {
            matrixSeriesMarkersRef.current = createSeriesMarkers(matrixSeriesMarkerRef.current, msMarkers);
        }
    }, [data, timestamps]);

    return null;
}
