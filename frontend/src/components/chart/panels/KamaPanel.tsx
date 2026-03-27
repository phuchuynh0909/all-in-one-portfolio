import { useEffect, useRef } from 'react';
import { LineSeries } from 'lightweight-charts';
import type { IChartApi, ISeriesApi, UTCTimestamp } from 'lightweight-charts';
import type { FutureOhlcResponse } from '../../../lib/services/future';

type KamaPanelProps = {
    chart: IChartApi;
    data: FutureOhlcResponse;
};

export default function KamaPanel({ chart, data }: KamaPanelProps) {
    const kama21Ref  = useRef<ISeriesApi<'Line'> | null>(null);
    const kama200Ref = useRef<ISeriesApi<'Line'> | null>(null);

    useEffect(() => {
        if (!chart) return;

        const kama21 = chart.addSeries(LineSeries, {
            color: '#f97316',
            lineWidth: 1,
            title: 'KAMA 21',
            priceScaleId: 'right',
            lastValueVisible: true,
            priceLineVisible: false,
        }, 0);
        kama21Ref.current = kama21;

        const kama200 = chart.addSeries(LineSeries, {
            color: '#a78bfa',
            lineWidth: 2,
            title: 'KAMA 200',
            priceScaleId: 'right',
            lastValueVisible: true,
            priceLineVisible: false,
        }, 0);
        kama200Ref.current = kama200;

        return () => {
            if (chart) {
                try { chart.removeSeries(kama21);  } catch (_) { }
                try { chart.removeSeries(kama200); } catch (_) { }
            }
            kama21Ref.current  = null;
            kama200Ref.current = null;
        };
    }, [chart]);

    useEffect(() => {
        if (!data?.timestamps) return;

        const toPoints = (values: (number | null)[]) =>
            data.timestamps
                .map((ts, i) => {
                    const v = values[i];
                    if (v === null || v === undefined) return null;
                    return { time: (new Date(ts).getTime() / 1000) as UTCTimestamp, value: v };
                })
                .filter((p): p is { time: UTCTimestamp; value: number } => p !== null);

        kama21Ref.current?.setData(toPoints(data.indicators.kama_21));
        kama200Ref.current?.setData(toPoints(data.indicators.kama_200));
    }, [data]);

    return null;
}
