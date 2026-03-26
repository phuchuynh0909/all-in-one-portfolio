import { useEffect, useRef } from 'react';
import { LineSeries } from 'lightweight-charts';
import type { IChartApi, ISeriesApi, UTCTimestamp } from 'lightweight-charts';
import type { FutureOhlcResponse } from '../../../lib/services/future';

type BsiPanelProps = {
    chart: IChartApi;
    data: FutureOhlcResponse;
};

export default function BsiPanel({ chart, data }: BsiPanelProps) {
    const bsiRfSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
    const zeroLineRef = useRef<ISeriesApi<"Line"> | null>(null);

    useEffect(() => {
        if (!chart) return;

        const bsiRfSeries = chart.addSeries(LineSeries, {
            color: '#10a4f4',
            lineWidth: 2,
            priceFormat: {
                type: 'custom',
                formatter: (price: number) => price.toFixed(2),
            },
            title: 'BSI',
            priceScaleId: 'right',
        }, 1);
        bsiRfSeriesRef.current = bsiRfSeries;

        const defaultFixedLineConfig = {
            priceScaleId: 'right',
            priceLineVisible: true,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
        } as const;

        const zeroLine = chart.addSeries(LineSeries, {
            color: 'rgba(156, 163, 175, 0.3)',
            lineWidth: 1,
            ...defaultFixedLineConfig,
        }, 1);
        zeroLineRef.current = zeroLine;

        return () => {
            if (chart) {
                try {
                    chart.removeSeries(bsiRfSeries);
                    chart.removeSeries(zeroLine);
                } catch (e) {
                    console.warn('Error removing series:', e);
                }
            }
            bsiRfSeriesRef.current = null;
            zeroLineRef.current = null;
        };
    }, [chart]);

    useEffect(() => {
        if (!data || !data.timestamps) return;

        const bsiRfData = data.timestamps
            .map((ts, i) => {
                const value = data.indicators.bsi[i];
                if (value === null || value === undefined) return null;
                return {
                    time: (new Date(ts).getTime() / 1000) as UTCTimestamp,
                    value: value,
                };
            })
            .filter((item): item is { time: UTCTimestamp; value: number } => item !== null);

        bsiRfSeriesRef.current?.setData(bsiRfData);

        if (bsiRfData.length > 0) {
            const zeroLineData = bsiRfData.map(item => ({
                time: item.time,
                value: 0,
            }));
            zeroLineRef.current?.setData(zeroLineData);
        }
    }, [data]);

    return null;
}
