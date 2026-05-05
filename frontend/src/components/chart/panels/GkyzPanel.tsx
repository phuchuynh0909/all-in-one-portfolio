import { useEffect, useRef } from 'react';
import { type IChartApi, type ISeriesApi, LineSeries, LineStyle } from 'lightweight-charts';
import { formatIndicatorData, createConstantLine } from '../../../lib/services/timeseries';

type GkyzPanelProps = {
    chart: IChartApi;
    data: any;
    timestamps: string[];
    visible?: boolean;
};

const PANE = 7;

export default function GkyzPanel({ chart, data, timestamps, visible }: GkyzPanelProps) {
    const seriesRef  = useRef<ISeriesApi<'Line'> | null>(null);
    const line08Ref  = useRef<ISeriesApi<'Line'> | null>(null);
    const line02Ref  = useRef<ISeriesApi<'Line'> | null>(null);

    useEffect(() => {
        if (!chart) return;

        const series = chart.addSeries(LineSeries, {
            color: '#f97316',
            lineWidth: 2,
            priceFormat: { type: 'custom', formatter: (p: number) => p.toFixed(3) },
            title: 'GKYZ',
            priceScaleId: 'right',
        }, PANE);
        series.moveToPane(PANE);
        seriesRef.current = series;

        const refLineConfig = {
            priceScaleId: 'right',
            lastValueVisible: false,
            crosshairMarkerVisible: false,
            lineStyle: LineStyle.Dashed,
            lineWidth: 1,
        } as const;

        const upper = chart.addSeries(LineSeries, {
            color: 'rgba(239, 68, 68, 0.6)',
            title: '0.8',
            ...refLineConfig,
        }, PANE);
        upper.moveToPane(PANE);
        line08Ref.current = upper;

        const lower = chart.addSeries(LineSeries, {
            color: 'rgba(34, 197, 94, 0.6)',
            title: '0.2',
            ...refLineConfig,
        }, PANE);
        lower.moveToPane(PANE);
        line02Ref.current = lower;

        return () => {
            const s  = seriesRef.current;
            const u  = line08Ref.current;
            const l  = line02Ref.current;
            seriesRef.current = null;
            line08Ref.current = null;
            line02Ref.current = null;
            try {
                if (s) chart.removeSeries(s);
                if (u) chart.removeSeries(u);
                if (l) chart.removeSeries(l);
            } catch (e) {
                console.warn('GkyzPanel cleanup error:', e);
            }
        };
    }, [chart]);

    useEffect(() => {
        const show = visible !== false;
        seriesRef.current?.applyOptions({ visible: show });
        line08Ref.current?.applyOptions({ visible: show });
        line02Ref.current?.applyOptions({ visible: show });
    }, [visible]);

    useEffect(() => {
        if (!data?.gkyz_volatility || !timestamps) return;

        const gkyzData = formatIndicatorData(timestamps, data.gkyz_volatility).map((pt) => ({
            ...pt,
            color: pt.value > 0.8 ? '#ef4444' : pt.value < 0.2 ? '#22c55e' : '#f97316',
        }));
        seriesRef.current?.setData(gkyzData);
        line08Ref.current?.setData(createConstantLine(gkyzData, 0.8));
        line02Ref.current?.setData(createConstantLine(gkyzData, 0.2));
    }, [data, timestamps]);

    return null;
}
