import { useEffect, useRef } from 'react';
import { IChartApi, ISeriesApi, LineSeries } from 'lightweight-charts';
import {
    formatIndicatorData,
    createConstantLine
} from '../../../lib/services/timeseries';

type VolatilityPanelProps = {
    chart: IChartApi;
    data: any; // Indicators data
    timestamps: string[];
    yzVisible?: boolean;
    kalmanVisible?: boolean;
};

export default function VolatilityPanel({ chart, data, timestamps, yzVisible, kalmanVisible }: VolatilityPanelProps) {
    const yzVolatilitySeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
    const kalmanZscoreSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
    const kalmanZscoreUpperRef = useRef<ISeriesApi<"Line"> | null>(null);
    const kalmanZscoreLowerRef = useRef<ISeriesApi<"Line"> | null>(null);

    // Toggle YZ Volatility visibility
    useEffect(() => {
        yzVolatilitySeriesRef.current?.applyOptions({ visible: yzVisible !== false });
    }, [yzVisible]);

    // Toggle Kalman Z-Score visibility
    useEffect(() => {
        const show = kalmanVisible !== false;
        kalmanZscoreSeriesRef.current?.applyOptions({ visible: show });
        kalmanZscoreUpperRef.current?.applyOptions({ visible: show });
        kalmanZscoreLowerRef.current?.applyOptions({ visible: show });
    }, [kalmanVisible]);

    useEffect(() => {
        if (!chart) return;

        // Create Yang-Zhang Volatility series
        const yzVolatilitySeries = chart.addSeries(LineSeries, {
            color: '#ec4899',  // Pink
            lineWidth: 2,
            priceFormat: {
                type: 'custom',
                formatter: (price: number) => price.toFixed(4),
            },
            title: 'YZ Volatility',
            priceScaleId: 'right',
        }, 3);
        yzVolatilitySeries.moveToPane(3);
        yzVolatilitySeriesRef.current = yzVolatilitySeries;

        // Create Kalman Z-Score series in a separate pane
        const kalmanZscoreSeries = chart.addSeries(LineSeries, {
            color: '#06b6d4',  // Cyan
            lineWidth: 2,
            priceFormat: {
                type: 'custom',
                formatter: (price: number) => price.toFixed(2),
            },
            title: 'Kalman Z-Score',
            priceScaleId: 'right',
        }, 3);
        kalmanZscoreSeries.moveToPane(3);
        kalmanZscoreSeriesRef.current = kalmanZscoreSeries;

        // Shared config for helper/reference lines
        const defaultFixedLineConfig = {
            priceScaleId: 'right',
            priceLineVisible: true,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
        } as const;

        // Add horizontal lines for upper/lower bounds
        const kalmanZscoreUpper = chart.addSeries(LineSeries, {
            color: 'rgba(239, 68, 68, 0.4)',
            lineWidth: 1,
            ...defaultFixedLineConfig,
        }, 3);
        kalmanZscoreUpperRef.current = kalmanZscoreUpper;

        const kalmanZscoreLower = chart.addSeries(LineSeries, {
            color: 'rgba(34, 197, 94, 0.4)',
            lineWidth: 1,
            ...defaultFixedLineConfig,
        }, 3);
        kalmanZscoreLowerRef.current = kalmanZscoreLower;

        return () => {
            const yzSeries = yzVolatilitySeriesRef.current;
            const kalmanSeries = kalmanZscoreSeriesRef.current;
            const kalmanUpper = kalmanZscoreUpperRef.current;
            const kalmanLower = kalmanZscoreLowerRef.current;
            yzVolatilitySeriesRef.current = null;
            kalmanZscoreSeriesRef.current = null;
            kalmanZscoreUpperRef.current = null;
            kalmanZscoreLowerRef.current = null;
            if (!chart) return;
            try {
                if (yzSeries) chart.removeSeries(yzSeries);
                if (kalmanSeries) chart.removeSeries(kalmanSeries);
                if (kalmanUpper) chart.removeSeries(kalmanUpper);
                if (kalmanLower) chart.removeSeries(kalmanLower);
            } catch (e) {
                console.warn('Error removing series:', e);
            }
        };
    }, [chart]);

    // Update data
    useEffect(() => {
        if (!data || !timestamps) return;

        const yzVolatilityData = formatIndicatorData(timestamps, data.yz_volatility ?? []);
        yzVolatilitySeriesRef.current?.setData(yzVolatilityData);

        const kalmanZscoreData = formatIndicatorData(timestamps, data.kalman_zscore ?? []);
        kalmanZscoreSeriesRef.current?.setData(kalmanZscoreData);

        const kalmanUpperBound = createConstantLine(kalmanZscoreData, 2);
        const kalmanLowerBound = createConstantLine(kalmanZscoreData, -2);
        kalmanZscoreUpperRef.current?.setData(kalmanUpperBound);
        kalmanZscoreLowerRef.current?.setData(kalmanLowerBound);
    }, [data, timestamps]);

    return null;
}
