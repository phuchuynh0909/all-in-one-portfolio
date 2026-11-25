import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import test, { after } from 'node:test';
import { build } from 'esbuild';

const result = await build({
  stdin: { contents: `
    import React from 'react';
    import { renderToStaticMarkup } from 'react-dom/server';
    import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
    import { ThemeProvider } from '@mui/material/styles';
    import { createAppTheme } from './src/theme';
    import HealthStrengthChart from './src/components/sector/HealthStrengthChart';
    export function render(rows, mode = 'light') {
      const client = new QueryClient();
      client.setQueryData(['healthStrength', 3, 1], { rows, universe: 100, as_of: '2026-11-19' });
      return renderToStaticMarkup(React.createElement(QueryClientProvider, { client },
        React.createElement(ThemeProvider, { theme: createAppTheme(mode) },
          React.createElement(HealthStrengthChart, { level: 3, sectorId: 1 }))));
    }`, resolveDir: process.cwd(), loader: 'tsx' },
  bundle: true, write: false, format: 'cjs', platform: 'node', jsx: 'automatic',
  mainFields: ['module', 'main'], alias: { '@mui/icons-material': join(process.cwd(), 'node_modules/@mui/icons-material/esm') },
  define: { 'import.meta.env': '{}', 'process.env.NODE_ENV': '"production"' },
});
const directory = await mkdtemp(join(tmpdir(), 'health-strength-tests-'));
after(() => rm(directory, { recursive: true, force: true }));
const path = join(directory, 'chart.cjs');
await writeFile(path, result.outputFiles[0].text);
const { render } = (await import(pathToFileURL(path).href)).default;
const stock = { symbol: 'AAA', name: 'A', health: 80, strength: 88, previous_strength: 70, financial_period: '2026-09-30', health_peers: 10,
  components: { profitability: 90, capital: 80, growth: 70 }, excess_returns: { '21': 5, '63': 10, '126': 20 }, turnover_vnd: 10e9, unavailable: null };

test('renders both scores and a price trail in both themes with an explicit local-score definition', () => {
  for (const mode of ['light', 'dark']) {
    const html = render([stock], mode);
    assert.match(html, /1\/1 symbols plotted/);
    assert.match(html, /financial quality 80, price strength 88/);
    assert.match(html, /our peer score, not Titan Health/);
    assert.match(html, /5-session price trail/);
    assert.match(html, /rank points in 5 sessions/);
    assert.doesNotMatch(html, /(?:cx|cy)="(?:NaN|Infinity)"/);
  }
});

test('missing financial data is explained without inventing a point', () => {
  const html = render([{ ...stock, health: null, components: {}, unavailable: 'Missing comparable financial statements' }]);
  assert.match(html, /0\/1 symbols plotted/);
  assert.match(html, /Missing comparable financial statements/);
  assert.match(html, /No symbols have both scores/);
  assert.doesNotMatch(html, /aria-label="AAA: financial quality/);
});
