import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { POLICY_VERSION } from '@/content/privacy-policy.ru';
import { fetchMe } from '@/shared/api/endpoints/auth';
import { mockIsAuthenticated, mockLogin, resetMockUser } from '@/shared/api/mock';
import type { CurrentUser } from '@/shared/api/types';
import { env } from '@/shared/config/env';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/shared/ui/alert-dialog';
import { hasAnyRole, isAppRole, isSelectableRole, primaryRole, type AppRole } from './roles';
import {
  initKeycloak,
  keycloak,
  loginRedirect,
  logoutRedirect,
  registerRedirect,
} from './keycloak';

/**
 * Контекст аутентификации: keycloak-js (PKCE, check-sso) + профиль из GET /auth/me.
 * Роли, активная роль и согласия берутся ТОЛЬКО из /auth/me — единственный источник
 * истины для guard'ов и лэйаутов (CLAUDE.md «Keycloak и роли»): без роли → /choose-role,
 * без согласия → /consent, роль определяет лэйаут.
 */

export type AuthPhase =
  /** Идёт keycloak.init (check-sso) или загрузка профиля. */
  | 'initializing'
  /** Не аутентифицирован — доступен только /login. */
  | 'anonymous'
  /** Аутентифицирован, профиль загружен. */
  | 'ready'
  /** Keycloak или /auth/me недоступны (error — причина). */
  | 'error'
  /** Refresh-токен истёк — модалка «Сессия истекла». */
  | 'expired';

export interface AuthContextValue {
  phase: AuthPhase;
  /** Причина phase === 'error' (ApiError или ошибка keycloak-js). */
  error: unknown;
  user: CurrentUser | null;
  /** Только прикладные роли (candidate/employer/admin). */
  roles: AppRole[];
  /** Активная роль: `active_role` из /auth/me, иначе старшая из ролей. Определяет лэйаут. */
  activeRole: AppRole | null;
  /** Роль ещё не выбрана (нет candidate/employer). admin без выбора роли — тоже true. */
  needsRole: boolean;
  /** Согласие на обработку ПДн не дано или дано на прежнюю редакцию политики. */
  needsConsent: boolean;
  hasRole: (...allowed: AppRole[]) => boolean;
  login: (redirectPath?: string) => void;
  register: (redirectPath?: string) => void;
  logout: () => void;
  /** Повторить инициализацию после ошибки. */
  retry: () => void;
  /** Подменить профиль после POST /auth/role или /auth/consent (ответ = me). */
  setUser: (user: CurrentUser) => void;
  /** Перечитать /auth/me. */
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/** В режиме фикстур Keycloak не трогаем: «вход» — флаг в sessionStorage. */
async function authenticate(): Promise<boolean> {
  // Контур мини-аппа (/tma/*) живёт без Keycloak: авторизация — X-Telegram-Init-Data,
  // а check-sso внутри WebView Telegram бессмыслен и может не достучаться до Keycloak.
  if (window.location.pathname.startsWith('/tma')) return false;
  if (env.mockApi) return mockIsAuthenticated();
  return initKeycloak();
}

const login = (redirectPath?: string): void =>
  env.mockApi ? mockLogin(redirectPath) : loginRedirect(redirectPath);
const register = (redirectPath?: string): void =>
  env.mockApi ? mockLogin(redirectPath) : registerRedirect(redirectPath);

export function AuthProvider({ children }: { children: ReactNode }): ReactNode {
  const [phase, setPhase] = useState<AuthPhase>('initializing');
  const [error, setError] = useState<unknown>(null);
  const [user, setUserState] = useState<CurrentUser | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;

    if (!env.mockApi) {
      keycloak.onTokenExpired = () => {
        keycloak.updateToken(30).catch(() => undefined);
      };
      keycloak.onAuthRefreshError = () => {
        // keycloak-js зовёт колбэк и при сетевом сбое refresh (токен остаётся живым);
        // «сессия истекла» — только если токен действительно сброшен (при 400 от Keycloak
        // clearToken() вызывается до колбэка, поэтому authenticated уже false)
        if (!cancelled && !keycloak.authenticated) setPhase('expired');
      };
    }

    authenticate()
      .then(async (authenticated) => {
        if (cancelled) return;
        if (!authenticated) {
          setPhase('anonymous');
          return;
        }
        const profile = await fetchMe();
        if (cancelled) return;
        setUserState(profile);
        setError(null);
        setPhase('ready');
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        setError(reason);
        setPhase('error');
      });

    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const roles = useMemo<AppRole[]>(() => (user?.roles ?? []).filter(isAppRole), [user]);

  const activeRole = useMemo<AppRole | null>(() => {
    if (user?.active_role && isAppRole(user.active_role)) return user.active_role;
    return primaryRole(roles);
  }, [user, roles]);

  const needsRole = !roles.some(isSelectableRole);
  // новая редакция политики (POLICY_VERSION) требует повторного согласия
  const needsConsent =
    !user?.consent.pdn_at || user.consent.policy_version !== POLICY_VERSION;

  const hasRole = useCallback((...allowed: AppRole[]) => hasAnyRole(roles, allowed), [roles]);

  const retry = useCallback(() => {
    setPhase('initializing');
    setAttempt((n) => n + 1);
  }, []);

  const setUser = useCallback((next: CurrentUser) => setUserState(next), []);

  const refreshUser = useCallback(async () => {
    const profile = await fetchMe();
    setUserState(profile);
  }, []);

  const logout = useCallback(() => {
    if (env.mockApi) {
      resetMockUser();
      window.location.assign('/login');
      return;
    }
    logoutRedirect();
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      phase,
      error,
      user,
      roles,
      activeRole,
      needsRole,
      needsConsent,
      hasRole,
      login,
      register,
      logout,
      retry,
      setUser,
      refreshUser,
    }),
    [
      phase,
      error,
      user,
      roles,
      activeRole,
      needsRole,
      needsConsent,
      hasRole,
      logout,
      retry,
      setUser,
      refreshUser,
    ],
  );

  return (
    <AuthContext.Provider value={value}>
      {children}
      {/* Модалка «Сессия истекла»: AlertDialog — без закрытия по Esc/фону. */}
      <AlertDialog open={phase === 'expired'}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Сессия истекла</AlertDialogTitle>
            <AlertDialogDescription>
              Время сессии вышло. Войдите снова, чтобы продолжить — ответы в тесте сохраняются
              на сервере после каждого задания и не потеряются.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogAction onClick={() => loginRedirect(window.location.pathname)}>
              Войти заново
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth используется вне AuthProvider');
  }
  return ctx;
}
