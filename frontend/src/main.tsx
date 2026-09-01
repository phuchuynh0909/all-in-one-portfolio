import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { installAuthFetch } from './lib/auth/authFetch';
import './styles/global.css';

// Before the first render: a component that fetches on mount must already see
// the wrapped fetch.
installAuthFetch();

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
