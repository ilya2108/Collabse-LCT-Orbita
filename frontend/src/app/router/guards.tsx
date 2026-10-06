import { TriangleAlert } from 'lucide-react';
import { motion, useReducedMotion } from 'motion/react';
import type { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { errorMessage, isApiError } from '@/shared/api/errors';
import { useAuth } from '@/shared/auth/AuthContext';
import { homePathFor, type AppRole } from '@/shared/auth/roles';
import { Illustration } from '@/shared/illustrations';
import { motionTokens } from '@/shared/lib/motion';
import { useDocumentTitle } from '@/shared/lib/useDocumentTitle';
import { Button } from '@/shared/ui/button';

/**
 * Guard-экраны и guard'ы маршрутов. Правила онбординга (CLAUDE.md «Keycloak и роли»):
 * не аутентифицирован → /login; без роли → /choose-role; без согласия на ПДн → /consent;
 * роль определяет лэйаут (/candidate или /employer). Прямой URL не по роли → /403.
 */

/** Полноэкранный сплеш на время keycloak.init и загрузки /auth/me. */
export function AuthSplash(): ReactNode {
  const reduced = useReducedMotion() ?? false;
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-background">
      <motion.img
        src="/favicon.svg"
        alt="ФСП.Орбита"
        width={56}
        height={56}
        animate={reduced ? undefined : { opacity: [1, 0.45, 1] }}
        transition={{
          duration: motionTokens.duration.slow * 2,
          repeat: Infinity,
          ease: 'easeInOut',
        }}
      />
      <span className="text-sm text-muted-foreground" aria-live="polite">
        Загрузка…
      </span>
    </div>
  );
}

/**
 * Экран ошибки инициализации: Keycloak недоступен или backend не отвечает на /auth/me.
 * Отдельная ветка для insecure context: без window.crypto.subtle keycloak-js с PKCE S256
 * падает до первого сетевого запроса — «Повторить» бессмысленно, нужен адрес
 * http://localhost:<тот же порт> (secure context по спецификации).
 * `onLogout` — для стабильных 401/403 от /auth/me (удалённая учётная запись, чужой
 * audience): SSO-сессия Keycloak жива, и без «Выйти» пользователь застрял бы в «Повторить».
 */
export function AuthErrorScreen({
  title,
  error,
  onRetry,
  onLogout,
}: {
  title: string;
  error?: unknown;
  onRetry?: () => void;
  onLogout?: () => void;
}): ReactNode {
  const insecureContext =
    typeof window !== 'undefined' && (!window.isSecureContext || !window.crypto?.subtle);
  useDocumentTitle(title);

  if (insecureContext) {
    const port = window.location.port ? `:${window.location.port}` : '';
    const target = `http://localhost${port}${window.location.pathname}${window.location.search}`;
    return (
      <div className="flex min-h-screen items-center justify-center bg-background px-4">
        <div className="flex w-full max-w-md flex-col items-center gap-4 rounded-xl border border-border bg-card p-8 text-center shadow-card">
          <span className="flex size-12 items-center justify-center rounded-full bg-status-warning-tint text-status-warning-deep">
            <TriangleAlert className="size-6" aria-hidden="true" />
          </span>
          <h1 className="text-balance text-xl font-semibold">{title}</h1>
          <p className="text-sm text-muted-foreground">
            Текущий адрес не является безопасным контекстом браузера, вход через Keycloak (PKCE)
            невозможен. Откройте приложение по адресу{' '}
            <a href={target} className="text-primary-deep hover:underline">
              localhost
            </a>
            .
          </p>
          <Button asChild>
            <a href={target}>Открыть по адресу localhost</a>
          </Button>
        </div>
      </div>
    );
  }

  const fallback = new Error(
    'Сервис входа или API недоступны. Проверьте, что Keycloak (8080) и backend (8000) запущены.',
  );
  const shown = error ?? fallback;
  const requestId = isApiError(shown) ? shown.requestId : null;

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div role="alert" className="flex w-full max-w-md flex-col items-center gap-3 text-center">
        <Illustration name="error-broken" height={160} />
        <h1 className="text-balance text-xl font-semibold">{title}</h1>
        <p className="text-sm text-muted-foreground">{errorMessage(shown)}</p>
        {requestId ? (
          <p className="text-xs text-muted-foreground">Код обращения: {requestId}</p>
        ) : null}
        <div className="flex flex-wrap items-center justify-center gap-2">
          {onRetry ? <Button onClick={onRetry}>Повторить</Button> : null}
          {onLogout ? (
            <Button variant="outline" onClick={onLogout}>
              Выйти
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

/**
 * Guard аутентификации: неаутентифицированных уводит на /login
 * с сохранением исходного deep-link (вернёмся после логина).
 */
export function RequireAuth({ children }: { children: ReactNode }): ReactNode {
  const { phase, error, retry, logout } = useAuth();
  const location = useLocation();

  switch (phase) {
    case 'initializing':
      return <AuthSplash />;
    case 'error':
      return (
        <AuthErrorScreen
          title="Не удалось войти в систему"
          error={error}
          onRetry={retry}
          onLogout={logout}
        />
      );
    case 'anonymous':
      return (
        <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
      );
    default:
      return children;
  }
}

/**
 * Guard онбординга: роль выбрана и согласие на ПДн дано. Иначе — на нужный шаг.
 * Используется поверх кабинетов; сами /choose-role и /consent используют обратные
 * guard'ы (ниже), чтобы не зациклиться.
 */
export function RequireOnboarded({ children }: { children: ReactNode }): ReactNode {
  const { needsRole, needsConsent } = useAuth();
  const location = useLocation();
  if (needsRole) {
    return <Navigate to="/choose-role" replace state={{ from: location.pathname }} />;
  }
  if (needsConsent) {
    return <Navigate to="/consent" replace state={{ from: location.pathname }} />;
  }
  return children;
}

/** Guard шага «выбор роли»: если роль уже есть — дальше по онбордингу. */
export function RequireNoRole({ children }: { children: ReactNode }): ReactNode {
  const { needsRole, needsConsent, activeRole } = useAuth();
  if (!needsRole) {
    return <Navigate to={needsConsent ? '/consent' : homePathFor(activeRole)} replace />;
  }
  return children;
}

/** Guard шага «согласие»: без роли — назад к выбору; с согласием — в кабинет. */
export function RequireConsentPending({ children }: { children: ReactNode }): ReactNode {
  const { needsRole, needsConsent, activeRole } = useAuth();
  if (needsRole) return <Navigate to="/choose-role" replace />;
  if (!needsConsent) return <Navigate to={homePathFor(activeRole)} replace />;
  return children;
}

interface RequireRoleProps {
  roles: AppRole[];
  children: ReactNode;
}

/** Guard маршрута по ролям: прямой URL не по роли → редирект на /403. */
export function RequireRole({ roles, children }: RequireRoleProps): ReactNode {
  const { hasRole } = useAuth();
  if (!hasRole(...roles)) {
    return <Navigate to="/403" replace />;
  }
  return children;
}

/** Корень «/»: после онбординга — в кабинет активной роли. */
export function HomeRedirect(): ReactNode {
  const { activeRole } = useAuth();
  return <Navigate to={homePathFor(activeRole)} replace />;
}
