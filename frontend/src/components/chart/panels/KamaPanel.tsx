import { useEffect, useRef } from 'react';
import { LineSeries } from 'lightweight-charts';
import type { IChartApi, ISeriesApi, UTCTimestamp } from 'lightweight-charts';
import type { FutureOhlcResponse } from '../../../lib/services/future';

type KamaPanelProps = {
    chart: IChartApi;
    data: FutureOhlcResponse;
};

export default function KamaPanel({ chart, data }: KamaPanelProps) {
    const kamaRef = useRef<ISeriesApi<'Line'> | null>(null);

    useEffect(() => {
        if (!chart) return;

        const kama = chart.addSeries(LineSeries, {
            color: '#f97316',
            lineWidth: 1,
            title: 'KAMA',
            priceScaleId: 'right',
            lastValueVisible: true,
            priceLineVisible: false,
        }, 0);
        kamaRef.current = kama;

        return () => {
            if (chart) {
                try { chart.removeSeries(kama); } catch (_) {}
            }
            kamaRef.current = null;
        };
    }, [chart]);

    useEffect(() => {
        if (!data?.timestamps) return;

        const points = data.timestamps
            .map((ts, i) => {
                const v = data.indicators.kama[i];
                if (v === null || v === undefined) return null;
                return { time: (new Date(ts).getTime() / 1000) as UTCTimestamp, value: v };
            })
            .filter((p): p is { time: UTCTimestamp; value: number } => p !== null);

        kamaRef.current?.setData(points);
    }, [data]);

    return null;
}
