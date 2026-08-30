import { useEffect, useRef } from 'react';
import { LineSeries } from 'lightweight-charts';
import type { IChartApi, ISeriesApi, UTCTimestamp } from 'lightweight-charts';
import type { FutureOhlcResponse } from '../../../lib/services/future';

type ZScorePanelProps = {
    chart: IChartApi;
    data: FutureOhlcResponse;
    threshold?: number;
};

export default function ZScorePanel({ chart, data, threshold = 2.0 }: ZScorePanelProps) {
    const bsiNormSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
    const upperThresholdLineRef = useRef<ISeriesApi<"Line"> | null>(null);
    const lowerThresholdLineRef = useRef<ISeriesApi<"Line"> | null>(null);
    const zeroLineRef = useRef<ISeriesApi<"Line"> | null>(null);

    useEffect(() => {
        if (!chart) return;

        const bsiNormSeries = chart.addSeries(LineSeries, {
            color: '#f59e0b',
            lineWidth: 2,
            priceFormat: {
                type: 'custom',
                formatter: (price: number) => price.toFixed(2),
            },
            title: 'BSI Z-score',
            priceScaleId: 'right',
        }, 2);
        bsiNormSeriesRef.current = bsiNormSeries;

        const defaultFixedLineConfig = {
            priceScaleId: 'right',
            priceLineVisible: true,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
        } as const;

        const upperThresholdLine = chart.addSeries(LineSeries, {
            color: 'rgba(239, 68, 68, 0.6)',
            lineWidth: 1,
            ...defaultFixedLineConfig,
        }, 2);
        upperThresholdLineRef.current = upperThresholdLine;

        const lowerThresholdLine = chart.addSeries(LineSeries, {
            color: 'rgba(34, 197, 94, 0.6)',
            lineWidth: 1,
            ...defaultFixedLineConfig,
        }, 2);
        lowerThresholdLineRef.current = lowerThresholdLine;

        const zeroLine = chart.addSeries(LineSeries, {
            color: 'rgba(156, 163, 175, 0.3)',
            lineWidth: 1,
            ...defaultFixedLineConfig,
        }, 2);
        zeroLineRef.current = zeroLine;

        return () => {
            if (chart) {
                try {
                    if (bsiNormSeriesRef.current) {
                        chart.removeSeries(bsiNormSeriesRef.current);
                    }
                    if (upperThresholdLineRef.current) {
                        chart.removeSeries(upperThresholdLineRef.current);
                    }
                    if (lowerThresholdLineRef.current) {
                        chart.removeSeries(lowerThresholdLineRef.current);
                    }
                    if (zeroLineRef.current) {
                        chart.removeSeries(zeroLineRef.current);
                    }
                } catch (e) {
                    console.warn('Error removing series:', e);
                }
            }
            bsiNormSeriesRef.current = null;
            upperThresholdLineRef.current = null;
            lowerThresholdLineRef.current = null;
            zeroLineRef.current = null;
        };
    }, [chart]);

    useEffect(() => {
        if (!data || !data.timestamps) return;

        const bsiNormData = data.timestamps
            .map((ts, i) => {
                // The API exposes `bsi`; there is no `bsi_norm` field.
                const value = data.indicators.bsi[i];
                if (value === null || value === undefined) return null;
                return {
                    time: (new Date(ts).getTime() / 1000) as UTCTimestamp,
                    value: value,
                };
            })
            .filter((item): item is { time: UTCTimestamp; value: number } => item !== null);

        bsiNormSeriesRef.current?.setData(bsiNormData);

        if (bsiNormData.length > 0) {
            const upperData = bsiNormData.map(item => ({
                time: item.time,
                value: threshold,
            }));
            upperThresholdLineRef.current?.setData(upperData);

            const lowerData = bsiNormData.map(item => ({
                time: item.time,
                value: -threshold,
            }));
            lowerThresholdLineRef.current?.setData(lowerData);

            const zeroData = bsiNormData.map(item => ({
                time: item.time,
                value: 0,
            }));
            zeroLineRef.current?.setData(zeroData);
        }
    }, [data, threshold]);

    return null;
}
