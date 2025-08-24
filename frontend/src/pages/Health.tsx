import { useEffect, useState } from 'react';
import { apiGet } from '../lib/api';

export default function Health() {
  const [status, setStatus] = useState<string>('loading...');

  useEffect(() => {
    apiGet<{ status: string }>(`/health`)
      .then((data) => setStatus(data.status))
      .catch((e) => setStatus(`error: ${e.message}`));
  }, []);

  return (
    <div style={{ padding: 24 }}>
      <h2>API Health</h2>
      <pre>{status}</pre>
    </div>
  );
}
