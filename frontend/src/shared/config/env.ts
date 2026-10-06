/**
 * Конфигурация окружения фронтенда.
 *
 * Приоритет источников:
 * 1. window.__ENV__ — рантайм-конфиг из /config.js (генерируется envsubst'ом при старте
 *    контейнера из config.js.template), один docker-образ работает во всех окружениях;
 * 2. переменные сборки VITE_* (локальная разработка, `npm run dev`);
 * 3. дефолты локального стенда (`/api` через vite-прокси → 8000, Keycloak на localhost:8080,
 *    realm `fsp`, публичный клиент `fsp-frontend`).
 */

interface RuntimeEnv {
  API_BASE?: string;
  KEYCLOAK_URL?: string;
  KEYCLOAK_REALM?: string;
  KEYCLOAK_CLIENT_ID?: string;
  /** "1" — демо без backend и Keycloak: фикстуры вместо API (см. shared/api/mock.ts). */
  MOCK_API?: string;
}

declare global {
  interface Window {
    __ENV__?: RuntimeEnv;
  }
}

const runtime: RuntimeEnv = typeof window !== 'undefined' ? (window.__ENV__ ?? {}) : {};

function stripTrailingSlash(value: string): string {
  return value.endsWith('/') ? value.slice(0, -1) : value;
}

const apiBase = stripTrailingSlash(runtime.API_BASE ?? import.meta.env.VITE_API_URL ?? '/api');

function flag(value: string | undefined): boolean {
  return value === '1' || value === 'true';
}

export const env = {
  /** База API без версии, например `/api`. */
  apiBase,
  /** Полный префикс API v1: все запросы клиента идут на `${apiUrl}/...`. */
  apiUrl: `${apiBase}/v1`,
  keycloakUrl: stripTrailingSlash(
    runtime.KEYCLOAK_URL ?? import.meta.env.VITE_KEYCLOAK_URL ?? 'http://localhost:8080',
  ),
  keycloakRealm: runtime.KEYCLOAK_REALM ?? import.meta.env.VITE_KEYCLOAK_REALM ?? 'fsp',
  keycloakClientId:
    runtime.KEYCLOAK_CLIENT_ID ?? import.meta.env.VITE_KEYCLOAK_CLIENT_ID ?? 'fsp-frontend',
  /** Режим фикстур: UI без backend/Keycloak (только dev и демонстрация вёрстки). */
  mockApi: flag(runtime.MOCK_API) || flag(import.meta.env.VITE_MOCK_API),
} as const;
