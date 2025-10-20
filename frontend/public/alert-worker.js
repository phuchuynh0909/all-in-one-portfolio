/**
 * Web Worker for polling ISP alerts in the background
 * This runs in a separate thread to avoid blocking the UI
 */

let intervalId = null;
let apiBaseUrl = '';

// Worker message handler
self.addEventListener('message', async (event) => {
  const { type, payload } = event.data;

  switch (type) {
    case 'START_POLLING':
      startPolling(payload);
      break;
    
    case 'STOP_POLLING':
      stopPolling();
      break;
    
    case 'UPDATE_INTERVAL':
      updateInterval(payload);
      break;
    
    case 'FETCH_NOW':
      await fetchAlerts(payload);
      break;
    
    default:
      console.warn('Unknown message type:', type);
  }
});

function startPolling({ interval, apiUrl, params }) {
  apiBaseUrl = apiUrl;
  
  // Clear existing interval if any
  stopPolling();
  
  // Initial fetch
  fetchAlerts(params);
  
  // Set up recurring fetch
  intervalId = setInterval(() => {
    fetchAlerts(params);
  }, interval);
  
  self.postMessage({ type: 'POLLING_STARTED', interval });
}

function stopPolling() {
  if (intervalId) {
    clearInterval(intervalId);
    intervalId = null;
    self.postMessage({ type: 'POLLING_STOPPED' });
  }
}

function updateInterval({ interval, params }) {
  if (intervalId) {
    startPolling({ interval, apiUrl: apiBaseUrl, params });
  }
}

async function fetchAlerts(params) {
  try {
    const queryParams = new URLSearchParams();
    if (params.limit) queryParams.append('limit', params.limit.toString());
    if (params.since) queryParams.append('since', params.since.toString());
    
    // apiBaseUrl already includes /api/v1, so just append the endpoint path
    const url = `${apiBaseUrl}/isp/alerts/latest${queryParams.toString() ? `?${queryParams.toString()}` : ''}`;
    
    console.log('Worker fetching from:', url);
    
    const response = await fetch(url);
    
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }
    
    // Check if response is JSON
    const contentType = response.headers.get('content-type');
    if (!contentType || !contentType.includes('application/json')) {
      throw new Error(`Expected JSON but got ${contentType}. URL: ${url}`);
    }
    
    const data = await response.json();
    
    self.postMessage({
      type: 'ALERTS_FETCHED',
      payload: {
        alerts: data,
        timestamp: Date.now(),
      },
    });
  } catch (error) {
    console.error('Worker fetch error:', error);
    self.postMessage({
      type: 'FETCH_ERROR',
      payload: {
        error: error.message,
        timestamp: Date.now(),
      },
    });
  }
}

