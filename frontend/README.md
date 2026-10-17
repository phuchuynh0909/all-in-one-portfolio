# React + TypeScript + Vite

## Watchlists

Named watchlists, ordered symbols, repeated `###` section dividers and the selected list are saved to the existing MySQL database under your account. They are independent of chart layouts and load when you sign into the same account in another browser. Edits save automatically; the panel reports loading, saving and confirmed server saves. Failed writes remain in memory with a **Retry** action and are never saved to browser storage.

**Import browser watchlists** migrates lists from the old `chartWatchlists`/`chartWatchlist` browser keys when they differ from the server. Import preserves existing server lists and places conflicting browser copies in separate imported lists. Repeating an import keeps newer server edits. The old browser data remains a recovery backup only.

The authenticated `/api/v1/watchlists` API stores the account's complete watchlist library and selected list. Apply the MySQL migration with `docker compose exec -T backend alembic upgrade head`. Run `npm run test:watchlists` for the frontend queue/retry/account cleanup checks and `cd backend && pytest tests/test_watchlists.py -q` for MySQL persistence tests, whose writes are rolled back.

## Custom chart indicators

Use the chart header's layout picker → **Create new layout…** to name a custom indicator layout. Start from the current selection or choose **Start without indicators**. Layouts, the selected layout, indicator membership/parameters, OpenScript membership, native indicator settings/styles, drawings and pane state are saved in the existing MySQL database under your account. The three initial layouts remain available and also save their changes. Setups load across browsers when you sign into the same account.

Changes save automatically after a short delay. **Save layout** below the chart saves immediately; the adjacent status confirms the server save or reports an error. Switching layouts first saves the current setup and then restores the selected server version. **Import browser layouts** migrates old browser presets without overwriting existing server edits. Browser data is read only for migration, never used for new layout saves.

With a custom layout selected, open the layout picker and choose **Rename layout…** or **Delete layout…**. Renaming keeps its indicators and settings. Deleting asks for confirmation, removes the layout from your account, and switches to Layout 1; saved OpenScripts remain in your library. The three initial layouts cannot be renamed or deleted.

On **Charts**, open **OpenScript Editor** below the chart or from its header. Paste an OpenScript `version 1` indicator, use **Check** for diagnostics, then **Add to chart** (Ctrl/⌘ + Enter). The starter script plots a moving average over price. Omit `overlay = true` in `study(...)` to create a separate indicator pane.

**Save** stores the source without changing the running indicator. **Update on chart** applies the edited version. Choose saved scripts from the editor menu; **Remove** keeps the source and removes its chart instance, while the trash button deletes the saved script. Sources, applied versions and visibility are saved in the existing MySQL database under your signed-in account, and load in other browsers using that account. Unsaved editor changes stay in memory until you press Save. Input parameters and plot styles are available in TradingView's indicator settings.

Use **Import browser scripts** to migrate the old browser library into MySQL. Import preserves existing server versions and can be retried safely; old browser data is retained only as a recovery backup. New script sources are never saved to browser storage. Import missing scripts before importing layouts that reference them.

The authenticated `/api/v1/openscripts` API supports listing, saving, deleting, batch visibility updates and importing. `/api/v1/chart-layouts` stores layout setups, imports and the account's selected layout. Apply database migrations with `cd backend && alembic upgrade head` (or `docker compose exec -T backend alembic upgrade head`). Backend persistence tests use the existing MySQL database and rollback fixtures: `cd backend && pytest tests/test_openscripts.py tests/test_chart_layouts.py -q`.

The editor supports static line/histogram/column/marker plots, fills, levels and above/below signals, plus number, boolean and text inputs. Strategies, external data requests, dynamic colors, plot offsets, tables, alerts and background colors currently produce a validation error. Compiler errors leave the current chart running. Applying valid changes recreates the chart with its saved layout, preserving other studies and drawings; the edited indicator's settings reset to its script defaults.

Run `npm run test:indicators` for compiler, adapter and saved-script regression checks, and `npx tsc -b` for the full application type check.

This template provides a minimal setup to get React working in Vite with HMR and some ESLint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Babel](https://babeljs.io/) for Fast Refresh
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/) for Fast Refresh

## Expanding the ESLint configuration

If you are developing a production application, we recommend updating the configuration to enable type-aware lint rules:

```js
export default tseslint.config([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      // Other configs...

      // Remove tseslint.configs.recommended and replace with this
      ...tseslint.configs.recommendedTypeChecked,
      // Alternatively, use this for stricter rules
      ...tseslint.configs.strictTypeChecked,
      // Optionally, add this for stylistic rules
      ...tseslint.configs.stylisticTypeChecked,

      // Other configs...
    ],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
      // other options...
    },
  },
])
```

You can also install [eslint-plugin-react-x](https://github.com/Rel1cx/eslint-react/tree/main/packages/plugins/eslint-plugin-react-x) and [eslint-plugin-react-dom](https://github.com/Rel1cx/eslint-react/tree/main/packages/plugins/eslint-plugin-react-dom) for React-specific lint rules:

```js
// eslint.config.js
import reactX from 'eslint-plugin-react-x'
import reactDom from 'eslint-plugin-react-dom'

export default tseslint.config([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      // Other configs...
      // Enable lint rules for React
      reactX.configs['recommended-typescript'],
      // Enable lint rules for React DOM
      reactDom.configs.recommended,
    ],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
      // other options...
    },
  },
])
```
