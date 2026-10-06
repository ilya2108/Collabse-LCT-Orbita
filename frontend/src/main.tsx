import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
// Единственная точка входа стилей: Tailwind v4 + токены палитры ФСП +
// JetBrains Mono Variable self-hosted (@fontsource-variable/jetbrains-mono, woff2 в бандле).
import './styles/globals.css';
import { App } from './App';

const container = document.getElementById('root');
if (!container) {
  throw new Error('Не найден корневой элемент #root');
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
