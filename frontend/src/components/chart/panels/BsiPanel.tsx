import { useEffect, useRef } from 'react';
import { LineSeries, LineStyle } from 'lightweight-charts';
import type { IChartApi, ISeriesApi, UTCTimestamp } from 'lightweight-charts';
import type { FutureOhlcResponse } from '../../../lib/services/future';

type BsiPanelProps = {
    chart: IChartApi;
    data: FutureOhlcResponse;
};

export default function BsiPanel({ chart, data }: BsiPanelProps) {
    const bsiSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
    const qLoSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
    const qHiSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
    const zeroLineRef  = useRef<ISeriesApi<'Line'> | null>(null);

    useEffect(() => {
        if (!chart) return;

        const sharedScale = {
            priceScaleId: 'right',
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
        } as const;

        const qHiSeries = chart.addSeries(LineSeries, {
            ...sharedScale,
            color: '#ef5350',
            lineWidth: 2,
            lineStyle: LineStyle.Dashed,
            title: 'q_hi 95%',
        }, 1);
        qHiSeriesRef.current = qHiSeries;

        const qLoSeries = chart.addSeries(LineSeries, {
            ...sharedScale,
            color: '#26a69a',
            lineWidth: 2,
            lineStyle: LineStyle.Dashed,
            title: 'q_lo 5%',
        }, 1);
        qLoSeriesRef.current = qLoSeries;

        const bsiSeries = chart.addSeries(LineSeries, {
            color: '#10a4f4',
            lineWidth: 2,
            priceFormat: { type: 'custom', formatter: (p: number) => p.toFixed(0) },
            title: 'Hawkes BSI',
            priceScaleId: 'right',
        }, 1);
        bsiSeriesRef.current = bsiSeries;

        const zeroLine = chart.addSeries(LineSeries, {
            ...sharedScale,
            color: 'rgba(156, 163, 175, 0.4)',
            lineWidth: 1,
        }, 1);
        zeroLineRef.current = zeroLine;

        return () => {
            if (chart) {
                try { chart.removeSeries(bsiSeries);  } catch (_) {}
                try { chart.removeSeries(qHiSeries);  } catch (_) {}
                try { chart.removeSeries(qLoSeries);  } catch (_) {}
                try { chart.removeSeries(zeroLine);   } catch (_) {}
            }
            bsiSeriesRef.current = null;
            qHiSeriesRef.current = null;
            qLoSeriesRef.current = null;
            zeroLineRef.current  = null;
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

        const bsiPoints = toPoints(data.indicators.bsi);
        bsiSeriesRef.current?.setData(bsiPoints);
        qHiSeriesRef.current?.setData(toPoints(data.indicators.q_hi));
        qLoSeriesRef.current?.setData(toPoints(data.indicators.q_lo));

        if (bsiPoints.length > 0) {
            zeroLineRef.current?.setData(bsiPoints.map(p => ({ time: p.time, value: 0 })));
        }
    }, [data]);

    return null;
}
