import { lazy, Suspense, useEffect } from 'react';
import type { ReactNode } from 'react';
import { createBrowserRouter, useRouteError } from 'react-router-dom';
import { AppLayout } from '@/app/layout/AppLayout';
import { isApiError } from '@/shared/api/errors';
import { ErrorState, LoadingState } from '@/shared/ui/states';
import { CandidateHomePage } from '@/pages/candidate/CandidateHomePage';
import { CandidateInvitationsPage } from '@/pages/candidate/CandidateInvitationsPage';
import { CandidateSettingsPage } from '@/pages/candidate/CandidateSettingsPage';
import { CandidateCategoryPage } from '@/pages/candidate/CandidateCategoryPage';
import { CandidateProfilePage } from '@/pages/candidate/CandidateProfilePage';
import { CandidateSurveyPage } from '@/pages/candidate/CandidateSurveyPage';
import { CandidateTestPage } from '@/pages/candidate/CandidateTestPage';
import { CandidateVacanciesPage } from '@/pages/candidate/CandidateVacanciesPage';
import { CandidateWeeklyTaskPage } from '@/pages/candidate/CandidateStubPages';
import { ChooseRolePage } from '@/pages/ChooseRolePage';
import { ConsentPage } from '@/pages/ConsentPage';
import { EmployerBankPage } from '@/pages/employer/EmployerBankPage';
import { EmployerCompanyPage } from '@/pages/employer/EmployerCompanyPage';
import { EmployerHomePage } from '@/pages/employer/EmployerHomePage';
import { EmployerInvitationsPage } from '@/pages/employer/EmployerInvitationsPage';
import { EmployerInviteAcceptPage } from '@/pages/employer/EmployerInviteAcceptPage';
import { EmployerNeedsPage } from '@/pages/employer/EmployerNeedsPage';
import { EmployerPipelinePage } from '@/pages/employer/EmployerPipelinePage';
import { EmployerSelectionPage } from '@/pages/employer/EmployerSelectionPage';
import { EmployerSettingsPage } from '@/pages/employer/EmployerSettingsPage';
import { EmployerVacanciesListPage as EmployerVacanciesPage } from '@/pages/employer/EmployerVacanciesListPage';
import { ForbiddenPage, NotFoundPage } from '@/pages/ErrorPages';
import { LoginPage } from '@/pages/LoginPage';
import {
  HomeRedirect,
  RequireAuth,
  RequireConsentPending,
  RequireNoRole,
  RequireOnboarded,
  RequireRole,
} from './guards';

/** Тяжёлый экран с ECharts подключается динамически — Vite режет его в отдельный чанк. */
const SkillsGraphPage = lazy(() =>
  import('@/pages/skills/SkillsGraphPage').then((m) => ({ default: m.SkillsGraphPage })),
);

/** Контур Telegram Mini App — отдельный чанк: основному приложению он не нужен. */
const TmaLayout = lazy(() => import('@/tma/TmaLayout').then((m) => ({ default: m.TmaLayout })));
const TmaHomePage = lazy(() =>
  import('@/tma/TmaHomePage').then((m) => ({ default: m.TmaHomePage })),
);
const TmaPipelinePage = lazy(() =>
  import('@/tma/TmaPipelinePage').then((m) => ({ default: m.TmaPipelinePage })),
);

/** Фолбэк Suspense на весь экран (TMA-чанк грузится вне AppLayout). */
function FullScreenLoading(): ReactNode {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-6">
      <LoadingState rows={6} card={false} className="w-full max-w-[420px]" />
    </div>
  );
}

/** Русское описание для UI вместо сырого сообщения рантайм-ошибки. */
const RENDER_ERROR_FALLBACK = new Error(
  'Экран не смог отрисоваться. Обновите страницу — если ошибка повторится, сообщите команде.',
);

/**
 * Фолбэк errorElement: вместо дефолтного «Unexpected Application Error!» React Router —
 * ErrorState с иллюстрацией и кнопкой обновления. `full` — вне layout (упал сам каркас).
 */
function RouteErrorScreen({ full = false }: { full?: boolean }): ReactNode {
  const error = useRouteError();
  useEffect(() => {
    // Реальная причина — в консоль; в UI стектрейс не показываем
    console.error('Ошибка рендера маршрута:', error);
  }, [error]);
  const state = (
    <ErrorState
      error={isApiError(error) ? error : RENDER_ERROR_FALLBACK}
      title="Что-то пошло не так"
      onRetry={() => window.location.reload()}
    />
  );
  if (full) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background px-6">{state}</div>
    );
  }
  return <div className="app-content">{state}</div>;
}

/**
 * Маршрутизация. Онбординг: /login → /choose-role → /consent → кабинет роли.
 * Кабинеты под общим AppLayout (меню по роли), ветки защищены RequireRole;
 * страница графа доступна обеим ролям внутри их лэйаута.
 */
export const router = createBrowserRouter([
  { path: '/login', element: <LoginPage />, errorElement: <RouteErrorScreen full /> },
  {
    // Публичная страница приглашения в компанию: информация без входа, принятие — после входа
    path: '/employer/invite/:token',
    element: <EmployerInviteAcceptPage />,
    errorElement: <RouteErrorScreen full />,
  },
  {
    // Telegram Mini App: ВНЕ RequireAuth и AppLayout — авторизация по X-Telegram-Init-Data
    path: '/tma',
    element: (
      <Suspense fallback={<FullScreenLoading />}>
        <TmaLayout />
      </Suspense>
    ),
    errorElement: <RouteErrorScreen full />,
    children: [
      {
        index: true,
        element: (
          <Suspense fallback={<LoadingState rows={6} />}>
            <TmaHomePage />
          </Suspense>
        ),
      },
      {
        path: 'needs/:needId',
        element: (
          <Suspense fallback={<LoadingState rows={6} />}>
            <TmaPipelinePage />
          </Suspense>
        ),
      },
    ],
  },
  {
    path: '/choose-role',
    element: (
      <RequireAuth>
        <RequireNoRole>
          <ChooseRolePage />
        </RequireNoRole>
      </RequireAuth>
    ),
    errorElement: <RouteErrorScreen full />,
  },
  {
    path: '/consent',
    element: (
      <RequireAuth>
        <RequireConsentPending>
          <ConsentPage />
        </RequireConsentPending>
      </RequireAuth>
    ),
    errorElement: <RouteErrorScreen full />,
  },
  {
    path: '/',
    element: (
      <RequireAuth>
        <RequireOnboarded>
          <AppLayout />
        </RequireOnboarded>
      </RequireAuth>
    ),
    errorElement: <RouteErrorScreen full />,
    children: [
      {
        errorElement: <RouteErrorScreen />,
        children: [
          { index: true, element: <HomeRedirect /> },
          {
            path: 'candidate',
            element: (
              <RequireRole roles={['candidate', 'admin']}>
                <CandidateHomePage />
              </RequireRole>
            ),
          },
          {
            path: 'candidate/profile',
            element: (
              <RequireRole roles={['candidate', 'admin']}>
                <CandidateProfilePage />
              </RequireRole>
            ),
          },
          {
            path: 'candidate/survey',
            element: (
              <RequireRole roles={['candidate', 'admin']}>
                <CandidateSurveyPage />
              </RequireRole>
            ),
          },
          {
            path: 'candidate/test',
            element: (
              <RequireRole roles={['candidate', 'admin']}>
                <CandidateTestPage />
              </RequireRole>
            ),
          },
          {
            path: 'candidate/category',
            element: (
              <RequireRole roles={['candidate', 'admin']}>
                <CandidateCategoryPage />
              </RequireRole>
            ),
          },
          {
            path: 'candidate/invitations',
            element: (
              <RequireRole roles={['candidate', 'admin']}>
                <CandidateInvitationsPage />
              </RequireRole>
            ),
          },
          {
            path: 'candidate/vacancies',
            element: (
              <RequireRole roles={['candidate', 'admin']}>
                <CandidateVacanciesPage />
              </RequireRole>
            ),
          },
          {
            path: 'candidate/weekly-task',
            element: (
              <RequireRole roles={['candidate', 'admin']}>
                <CandidateWeeklyTaskPage />
              </RequireRole>
            ),
          },
          {
            path: 'candidate/settings',
            element: (
              <RequireRole roles={['candidate', 'admin']}>
                <CandidateSettingsPage />
              </RequireRole>
            ),
          },
          {
            path: 'employer',
            element: (
              <RequireRole roles={['employer', 'admin']}>
                <EmployerHomePage />
              </RequireRole>
            ),
          },
          {
            path: 'employer/company',
            element: (
              <RequireRole roles={['employer', 'admin']}>
                <EmployerCompanyPage />
              </RequireRole>
            ),
          },
          {
            path: 'employer/needs',
            element: (
              <RequireRole roles={['employer', 'admin']}>
                <EmployerNeedsPage />
              </RequireRole>
            ),
          },
          {
            path: 'employer/selection',
            element: (
              <RequireRole roles={['employer', 'admin']}>
                <EmployerSelectionPage />
              </RequireRole>
            ),
          },
          {
            path: 'employer/pipeline',
            element: (
              <RequireRole roles={['employer', 'admin']}>
                <EmployerPipelinePage />
              </RequireRole>
            ),
          },
          {
            path: 'employer/settings',
            element: (
              <RequireRole roles={['employer', 'admin']}>
                <EmployerSettingsPage />
              </RequireRole>
            ),
          },
          {
            path: 'employer/bank',
            element: (
              <RequireRole roles={['employer', 'admin']}>
                <EmployerBankPage />
              </RequireRole>
            ),
          },
          {
            path: 'employer/invitations',
            element: (
              <RequireRole roles={['employer', 'admin']}>
                <EmployerInvitationsPage />
              </RequireRole>
            ),
          },
          {
            path: 'employer/vacancies',
            element: (
              <RequireRole roles={['employer', 'admin']}>
                <EmployerVacanciesPage />
              </RequireRole>
            ),
          },
          {
            path: 'skills/graph/:spec',
            element: (
              <Suspense fallback={<LoadingState rows={8} />}>
                <SkillsGraphPage />
              </Suspense>
            ),
          },
          { path: '403', element: <ForbiddenPage /> },
          { path: '*', element: <NotFoundPage /> },
        ],
      },
    ],
  },
]);
