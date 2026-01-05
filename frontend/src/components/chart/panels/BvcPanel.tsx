import { useEffect, useRef } from 'react';
import { IChartApi, ISeriesApi, LineSeries } from 'lightweight-charts';
import {
    formatIndicatorData,
    createConstantLine
} from '../../../lib/services/timeseries';

type BvcPanelProps = {
    chart: IChartApi;
    data: any; // Indicators data
    timestamps: string[];
};

export default function BvcPanel({ chart, data, timestamps }: BvcPanelProps) {
    const bvcSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
    const zeroLineRef = useRef<ISeriesApi<"Line"> | null>(null);

    useEffect(() => {
        if (!chart) return;

        // Create BVC series in a separate pane
        const bvcSeries = chart.addSeries(LineSeries, {
            color: '#a855f7',  // Purple
            lineWidth: 2,
            priceFormat: {
                type: 'custom',
                formatter: (price: number) => price.toFixed(2),
            },
            title: 'BVC',
            priceScaleId: 'right',
        }, 2);
        bvcSeries.moveToPane(2);
        bvcSeriesRef.current = bvcSeries;

        // Shared config for helper/reference lines
        const defaultFixedLineConfig = {
            priceScaleId: 'right',
            priceLineVisible: true,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
        } as const;

        const zeroLine = chart.addSeries(LineSeries, {
            color: 'rgba(156, 163, 175, 0.4)',
            lineWidth: 1,
            ...defaultFixedLineConfig,
        }, 2);
        zeroLineRef.current = zeroLine;

        return () => {
            if (chart) {
                try {
                    chart.removeSeries(bvcSeries);
                    chart.removeSeries(zeroLine);
                } catch (e) {
                    console.warn('Error removing series:', e);
                }
            }
            bvcSeriesRef.current = null;
            zeroLineRef.current = null;
        };
    }, [chart]);

    // Update data
    useEffect(() => {
        if (!data || !timestamps) return;

        const bvcData = formatIndicatorData(timestamps, data.bvc ?? []);
        bvcSeriesRef.current?.setData(bvcData);

        const zeroLineData = createConstantLine(bvcData, 0);
        zeroLineRef.current?.setData(zeroLineData);
    }, [data, timestamps]);

    return null;
}
