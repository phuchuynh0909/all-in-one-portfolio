import { API_BASE_URL } from '../api';
import { parseUserScripts, type UserScript } from '../tv/userScripts';

interface SavedScript {
  id: string;
  title: string;
  source: string;
  applied_source: string | null;
  visible: boolean;
}

async function request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE_URL}/openscripts${path}`, {
    method,
    ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  });
  if (!response.ok) {
    let message = `Could not ${method === 'GET' ? 'load' : 'save'} scripts (${response.status}).`;
    try {
      const error = await response.json();
      if (typeof error.detail === 'string') message = error.detail;
      else if (Array.isArray(error.detail)) message = error.detail.map((item: { msg: string }) => item.msg).join('; ');
    } catch { /* Keep the HTTP error when the server returns no JSON. */ }
    throw new Error(message);
  }
  return response.status === 204 ? undefined as T : await response.json() as T;
}

function decode(scripts: SavedScript[]): UserScript[] {
  return parseUserScripts(scripts.map((script) => ({ ...script, appliedSource: script.applied_source ?? undefined })));
}

function encode(script: UserScript): SavedScript {
  return { id: script.id, title: script.title, source: script.source, applied_source: script.appliedSource ?? null, visible: script.visible };
}

export async function listOpenScripts(): Promise<UserScript[]> {
  return decode(await request<SavedScript[]>(''));
}

export async function saveOpenScript(script: UserScript): Promise<UserScript> {
  const { id, ...body } = encode(script);
  return decode([await request<SavedScript>(`/${encodeURIComponent(id)}`, 'PUT', body)])[0];
}

export function deleteOpenScript(id: string): Promise<void> {
  return request(`/${encodeURIComponent(id)}`, 'DELETE');
}

export function updateOpenScriptVisibility(changes: Pick<UserScript, 'id' | 'visible'>[]): Promise<void> {
  return request('/visibility', 'PATCH', changes);
}

export async function importOpenScripts(scripts: UserScript[]): Promise<UserScript[]> {
  return decode(await request<SavedScript[]>('/import', 'POST', scripts.map(encode)));
}
