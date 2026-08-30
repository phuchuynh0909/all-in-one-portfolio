import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import type { Catalog } from './types';

/**
 * Static files, served from a path that deliberately differs from the SPA
 * route. Naming this directory "/experiments" collides with the React route of
 * the same name: the dev server only survives it because its HTML-fallback
 * middleware runs before static resolution, and nginx offers no such guarantee.
 */
export const EXPERIMENTS_BASE_URL =
  (import.meta.env.VITE_EXPERIMENTS_BASE_URL as string | undefined) ?? '/experiment-data';

export function experimentFileUrl(relPath: string): string {
  return `${EXPERIMENTS_BASE_URL}/${relPath}`;
}

export async function fetchCatalog(): Promise<Catalog> {
  const res = await fetch(`${EXPERIMENTS_BASE_URL}/catalog.json`, { cache: 'no-store' });
  if (res.status === 404) {
    // An empty store is a normal state, not an error.
    return { schema_version: 1, runs: [] };
  }
  if (!res.ok) {
    throw new Error(`Failed to load experiment catalog: ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as Catalog;
}

export function useCatalog(): UseQueryResult<Catalog> {
  return useQuery({ queryKey: ['experiments', 'catalog'], queryFn: fetchCatalog });
}
