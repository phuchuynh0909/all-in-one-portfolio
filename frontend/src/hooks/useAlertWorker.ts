/**
 * Custom hook for using Web Worker to poll alerts
 */
import { useEffect, useRef, useCallback, useState } from 'react';
import type { ISPAlert } from '../lib/services/ispAlerts';
import { API_BASE_URL } from '../lib/api';

interface UseAlertWorkerOptions {
  enabled: boolean;
  interval: number;
  onAlertsReceived: (alerts: ISPAlert[]) => void;
  onError: (error: string) => void;
  apiBaseUrl?: string;
}

interface WorkerMessage {
  type: string;
  payload?: any;
  interval?: number;
}

export function useAlertWorker({
  enabled,
  interval,
  onAlertsReceived,
  onError,
  apiBaseUrl = API_BASE_URL,
}: UseAlertWorkerOptions) {
  const workerRef = useRef<Worker | null>(null);
  const [isPolling, setIsPolling] = useState(false);

  // Initialize worker
  useEffect(() => {
    // Create worker
    workerRef.current = new Worker('/alert-worker.js');

    // Set up message handler
    workerRef.current.onmessage = (event: MessageEvent<WorkerMessage>) => {
      const { type, payload } = event.data;

      switch (type) {
        case 'ALERTS_FETCHED':
          onAlertsReceived(payload.alerts);
          break;

        case 'FETCH_ERROR':
          onError(payload.error);
          break;

        case 'POLLING_STARTED':
          setIsPolling(true);
          break;

        case 'POLLING_STOPPED':
          setIsPolling(false);
          break;

        default:
          console.warn('Unknown worker message type:', type);
      }
    };

    // Set up error handler
    workerRef.current.onerror = (error) => {
      console.error('Worker error:', error);
      onError('Worker error occurred');
    };

    // Cleanup on unmount
    return () => {
      if (workerRef.current) {
        workerRef.current.terminate();
        workerRef.current = null;
      }
    };
  }, [onAlertsReceived, onError]);

  // Start/stop polling based on enabled flag
  useEffect(() => {
    if (!workerRef.current) return;

    if (enabled) {
      workerRef.current.postMessage({
        type: 'START_POLLING',
        payload: {
          interval,
          apiUrl: apiBaseUrl,
          params: {
            limit: 1000,
          },
        },
      });
    } else {
      workerRef.current.postMessage({ type: 'STOP_POLLING' });
    }
  }, [enabled, interval, apiBaseUrl]);

  // Update interval
  useEffect(() => {
    if (!workerRef.current || !isPolling) return;

    workerRef.current.postMessage({
      type: 'UPDATE_INTERVAL',
      payload: {
        interval,
        params: {
          limit: 1000,
        },
      },
    });
  }, [interval, isPolling]);

  // Fetch with specific parameters
  const fetchWithParams = useCallback((params: { limit?: number; since?: number }) => {
    if (!workerRef.current) return;

    workerRef.current.postMessage({
      type: 'FETCH_NOW',
      payload: params,
    });
  }, []);

  return {
    isPolling,
    fetchWithParams,
  };
}

