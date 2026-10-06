import Keycloak from 'keycloak-js';
import { env } from '@/shared/config/env';

/**
 * Экземпляр keycloak-js (OIDC Authorization Code + PKCE, public client `fsp-frontend`
 * realm'а `fsp`). Регистрация и подтверждение email делаются самим Keycloak
 * (registrationAllowed, verifyEmail → письмо в Mailpit).
 *
 * Экспорт — `let`: экземпляр нельзя инициализировать повторно («A 'Keycloak' instance
 * can only be initialized once»), поэтому после неудачного init он пересоздаётся
 * (`resetKeycloak`), и кнопка «Повторить» на экране ошибки действительно повторяет вход.
 * Импортёры получают живую привязку ES-модуля и видят новый экземпляр.
 */
function createKeycloak(): Keycloak {
  return new Keycloak({
    url: env.keycloakUrl,
    realm: env.keycloakRealm,
    clientId: env.keycloakClientId,
  });
}

export let keycloak: Keycloak = createKeycloak();

let initPromise: Promise<boolean> | null = null;

/** Пересоздать экземпляр после сбоя init (Keycloak ещё стартует, сеть, битый realm). */
export function resetKeycloak(): void {
  keycloak = createKeycloak();
  initPromise = null;
}

/**
 * Инициализация — ровно один раз на экземпляр (двойной mount эффектов в StrictMode
 * не должен вызывать повторный init). Отклонённый промис не кэшируется: экземпляр
 * пересоздаётся, следующий вызов стартует заново.
 *
 * pkceMethod 'S256' считает challenge через Web Crypto (crypto.subtle), а он есть
 * только в secure context: http://localhost и *.localhost подходят, http://<ip> — нет.
 * Insecure context ловит guard в app/router/guards.tsx.
 */
export function initKeycloak(): Promise<boolean> {
  initPromise ??= keycloak
    .init({
      onLoad: 'check-sso',
      pkceMethod: 'S256',
      silentCheckSsoRedirectUri: `${window.location.origin}/silent-check-sso.html`,
      checkLoginIframe: false,
    })
    .catch((reason: unknown) => {
      resetKeycloak();
      throw reason;
    });
  return initPromise;
}

/**
 * Актуальный access token: тихо обновляет, если жить осталось < minValidity секунд.
 * null — пользователь не аутентифицирован или refresh не удался
 * (в этом случае keycloak-js вызовет onAuthRefreshError → модалка «Сессия истекла»,
 * если токен действительно сброшен, см. AuthContext).
 */
export async function getFreshToken(minValidity = 30): Promise<string | null> {
  if (!keycloak.authenticated) return null;
  try {
    await keycloak.updateToken(minValidity);
  } catch {
    return null;
  }
  return keycloak.token ?? null;
}

function redirectUri(path?: string): string {
  return `${window.location.origin}${path ?? '/'}`;
}

/** Редирект на страницу входа Keycloak («ФСП ID / email») с возвратом на указанный путь SPA. */
export function loginRedirect(redirectPath?: string): void {
  void keycloak.login({ redirectUri: redirectUri(redirectPath) });
}

/** Редирект на страницу регистрации Keycloak; после подтверждения email вернёмся в SPA. */
export function registerRedirect(redirectPath?: string): void {
  void keycloak.register({ redirectUri: redirectUri(redirectPath) });
}

export function logoutRedirect(): void {
  void keycloak.logout({ redirectUri: `${window.location.origin}/login` });
}
