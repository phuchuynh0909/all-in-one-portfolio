export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1'

/**
 * Turn a failed response into an Error carrying the server's own message.
 * FastAPI puts the actionable text in `detail`; without this every 4xx reaches
 * the UI as a bare status code and the reason is lost.
 */
async function toError(res: Response, method: string, path: string): Promise<Error> {
  let detail = '';
  try {
    const body = await res.json();
    if (typeof body?.detail === 'string') detail = body.detail;
    else if (Array.isArray(body?.detail)) detail = body.detail.map((d: any) => d?.msg).filter(Boolean).join('; ');
  } catch {
    // Body was empty or not JSON; the status code is all we have.
  }
  return new Error(detail || `${method} ${path} failed: ${res.status}`);
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`);
  if (!res.ok) throw await toError(res, 'GET', path);
  return (await res.json()) as T;
}

export async function apiPost<T>(path: string, data: unknown): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw await toError(res, 'POST', path);
  return (await res.json()) as T;
}
