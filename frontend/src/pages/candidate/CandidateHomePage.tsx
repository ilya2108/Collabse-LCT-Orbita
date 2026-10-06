import { useQuery } from '@tanstack/react-query';
import { ArrowRight, ListChecks, Medal, Share2, Sparkles, UserRound, type LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { fetchMyProfile } from '@/shared/api/endpoints/candidates';
import { isApiError } from '@/shared/api/errors';
import { useAuth } from '@/shared/auth/AuthContext';
import { specializationLabel } from '@/shared/config/labels';
import { Illustration } from '@/shared/illustrations';
import { cn } from '@/shared/lib/cn';
import { Button } from '@/shared/ui/button';
import { StaggerGrid, StaggerItem } from '@/shared/ui/detail-blocks';
import { PageHeader } from '@/shared/ui/page-header';
import { ErrorState } from '@/shared/ui/states';
import { Stepper } from '@/shared/ui/stepper';

/**
 * Главная кандидата — «Путь до категории»: степпер из четырёх шагов (профиль →
 * опрос и свершения → тест → категория) и карточки шагов с пустыми состояниями.
 * На день 6 все шаги пустые; текущий шаг вычисляется из GET /candidates/me
 * (404 — профиль ещё не создан → шаг 0). Ошибки API, кроме 404, показываются
 * состоянием ошибки с «Повторить», без падения страницы.
 */

interface PathStep {
  id: string;
  title: string;
  hint: string;
  icon: LucideIcon;
  description: string;
  cta: { label: string; to: string };
}

const STEPS: PathStep[] = [
  {
    id: 'profile',
    title: 'Профиль',
    hint: 'Псевдоним, город, стек',
    icon: UserRound,
    description:
      'Заполните анкету: город, отрасль, заявленный стек. Контакты — по желанию, они шифруются.',
    cta: { label: 'Заполнить профиль', to: '/candidate/profile' },
  },
  {
    id: 'survey',
    title: 'Опрос и свершения',
    hint: 'Что вы делали сами',
    icon: Sparkles,
    description:
      'Расскажите о реальных результатах — они подскажут, какие узлы графа проверять в первую очередь.',
    cta: { label: 'К опросу', to: '/candidate/survey' },
  },
  {
    id: 'test',
    title: 'Тест',
    hint: 'Адаптивная проверка',
    icon: ListChecks,
    description:
      'Одно задание на экране, таймер, автосохранение. Ответы оценивает судья с рубрикой.',
    cta: { label: 'Пройти тест', to: '/candidate/test' },
  },
  {
    id: 'category',
    title: 'Категория',
    hint: 'Специализация × грейд',
    icon: Medal,
    description:
      'Результат ложится на граф: активные узлы, связность Q, грейд. Дальше — приглашения.',
    cta: { label: 'О категории', to: '/candidate/category' },
  },
];

export function CandidateHomePage(): ReactNode {
  const { user } = useAuth();

  const profileQuery = useQuery({
    queryKey: ['candidates', 'me'],
    queryFn: ({ signal }) => fetchMyProfile(signal),
    retry: false,
  });

  // 404/501 от backend — штатно (старый контракт): профиля ещё нет, путь начинается с первого шага.
  const softMissing =
    profileQuery.isError &&
    isApiError(profileQuery.error) &&
    (profileQuery.error.status === 404 || profileQuery.error.status === 501);
  const hardError = profileQuery.isError && !softMissing;
  const profile = profileQuery.data ?? null;
  // backend создаёт пустой профиль при первом GET — шаг «Профиль» засчитываем по заполненным
  // полям; специализация — ключевое поле (без неё не создать сессию теста, 409 profile_incomplete)
  const profileFilled =
    profile !== null &&
    (profile.specialization !== null ||
      profile.city !== null ||
      profile.stack_tags_declared.length > 0);
  const current = profileFilled ? 1 : 0;
  // Граф показываем по специализации кандидата; пока она не выбрана — бэкенд-граф по умолчанию
  const graphSpec = profile?.specialization ?? 'be';

  return (
    <>
      <PageHeader
        title="Путь до категории"
        subtitle={
          profile
            ? `Псевдоним ${profile.pseudonym} · шаг ${current + 1} из ${STEPS.length}`
            : `Четыре шага от регистрации до категории${user?.email ? ` · ${user.email}` : ''}`
        }
        extra={
          <Button variant="outline" asChild>
            <Link to={`/skills/graph/${graphSpec}`}>
              <Share2 aria-hidden="true" />
              Граф навыков
            </Link>
          </Button>
        }
      />

      {/* На узких экранах степпер прокручивается по горизонтали, а не ломает карточку */}
      <section className="overflow-x-auto rounded-lg border border-border bg-card p-5 shadow-card">
        <div className="min-w-[640px]">
          <Stepper steps={STEPS.map(({ id, title, hint }) => ({ id, title, hint }))} current={current} />
        </div>
      </section>

      {hardError ? (
        <div className="mt-4 rounded-lg border border-border bg-card shadow-card">
          <ErrorState
            error={profileQuery.error}
            title="Не удалось загрузить профиль"
            onRetry={() => void profileQuery.refetch()}
            compact
          />
        </div>
      ) : null}

      <StaggerGrid className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {STEPS.map((step, index) => {
          const Icon = step.icon;
          const active = index === current;
          const done = index < current;
          return (
            <StaggerItem key={step.id} className="h-full">
              <div
                className={cn(
                  'flex h-full flex-col gap-3 rounded-lg border bg-card p-5 shadow-card',
                  active ? 'border-primary ring-1 ring-primary' : 'border-border',
                )}
              >
                <div className="flex items-center justify-between">
                  <span
                    className={cn(
                      'flex size-10 items-center justify-center rounded-md',
                      active
                        ? 'bg-primary text-primary-foreground'
                        : done
                          ? 'bg-status-success-tint text-status-success-deep'
                          : 'bg-muted text-muted-foreground',
                    )}
                  >
                    <Icon className="size-5" aria-hidden="true" />
                  </span>
                  <span className="text-xs font-medium text-muted-foreground">Шаг {index + 1}</span>
                </div>
                <span className="text-sm font-semibold">{step.title}</span>
                <span className="flex-1 text-sm leading-relaxed text-muted-foreground">
                  {step.description}
                </span>
                <Button variant={active ? 'default' : 'outline'} size="sm" asChild className="w-fit">
                  <Link to={step.cta.to}>
                    {step.cta.label}
                    <ArrowRight aria-hidden="true" />
                  </Link>
                </Button>
              </div>
            </StaggerItem>
          );
        })}
      </StaggerGrid>

      <section className="mt-4 grid gap-4 lg:grid-cols-[1fr_320px]">
        <div className="rounded-lg border border-border bg-card p-5 shadow-card">
          <h2 className="text-base font-semibold">Ваш граф навыков</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Подтверждённых узлов пока нет. После теста здесь появится граф с активными узлами и их
            свежестью: «свежо», «тускнеет», «погасло».
          </p>
          <div className="mt-4 flex flex-col items-center gap-3 rounded-md bg-muted/50 py-6 text-center">
            <Illustration name="graph-empty" height={128} />
            <Button variant="link" asChild>
              <Link to={`/skills/graph/${graphSpec}`}>
                {profile?.specialization
                  ? `Посмотреть эталонный граф — ${specializationLabel(profile.specialization)}`
                  : 'Посмотреть эталонный граф специализации'}
              </Link>
            </Button>
          </div>
        </div>
        <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-5 shadow-card">
          <h2 className="text-base font-semibold">Приглашения</h2>
          <p className="text-sm text-muted-foreground">
            Появятся после категории и публикации профиля. Работодатель приходит с зарплатой
            «от–до» и объяснением «почему вы».
          </p>
          <p className="mt-auto text-xs text-muted-foreground">
            Профиль {user?.consent.publish_at ? 'опубликован' : 'скрыт от работодателей'} —
            изменить можно в настройках.
          </p>
        </div>
      </section>
    </>
  );
}
