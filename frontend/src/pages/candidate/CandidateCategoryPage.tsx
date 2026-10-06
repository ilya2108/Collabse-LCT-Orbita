import { useQuery } from '@tanstack/react-query';
import {
  BadgeCheck,
  CheckCircle2,
  CircleDot,
  Loader2,
  Mail,
  Share2,
  Sparkles,
  XCircle,
} from 'lucide-react';
import type { ReactNode } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { fetchMyCategory, fetchMyGraphSummary } from '@/shared/api/endpoints/candidates';
import {
  fetchLastTestReview,
  LAST_REVIEW_QUERY_KEY,
} from '@/shared/api/endpoints/assessment';
import { isApiError } from '@/shared/api/errors';
import type {
  TestFinishResult,
  TestNodeState,
  TestOutcome,
  TestReviewItem,
  TestReviewOut,
} from '@/shared/api/types';
import {
  gradeLabel,
  nodeStateLabel,
  specializationLabel,
  TEST_MODE_LABELS,
  testOutcomeLabel,
} from '@/shared/config/labels';
import { formatDateTime } from '@/shared/lib/format';
import { Alert, AlertDescription, AlertTitle } from '@/shared/ui/alert';
import { Button } from '@/shared/ui/button';
import { SectionCard } from '@/shared/ui/detail-blocks';
import { Illustration } from '@/shared/illustrations';
import { IntegrityBadge } from '@/shared/ui/integrity-badge';
import { PageHeader } from '@/shared/ui/page-header';
import { ErrorState, LoadingState } from '@/shared/ui/states';
import { StatusBadge, type StatusBadgeProps } from '@/shared/ui/status-badge';
import { LAST_TEST_RESULT_KEY } from './CandidateTestPage';

/**
 * «Категория и грейд»: специализация × подтверждённый грейд (GET /candidates/me/category),
 * уровень связности Q и разбор последнего теста (GET /assessment/sessions/last/review:
 * узлы, задания, критерии рубрики и советы «что подтянуть»; 404 — разбора нет).
 * Итог finish со страницы теста (state роутера + sessionStorage) остаётся фолбэком.
 * Цвета состояний — только семантическими токенами.
 */

/** fresh=success, fading=warning, expired/off — muted (бейдж без семантического статуса). */
const NODE_STATE_STATUS: Partial<Record<TestNodeState, StatusBadgeProps['status']>> = {
  fresh: 'success',
  fading: 'warning',
};

/** Итог сессии: подтверждён — success, не подтверждён — warning (без «красной» паники). */
const OUTCOME_STATUS: Record<TestOutcome, StatusBadgeProps['status']> = {
  strong: 'success',
  passed: 'success',
  failed: 'warning',
};

/** Итог последнего finish: из state роутера (переход с теста) или sessionStorage. */
function readLastResult(stateValue: unknown): TestFinishResult | null {
  const fromState = (stateValue as { finish?: TestFinishResult } | null)?.finish;
  if (fromState) return fromState;
  try {
    const raw = sessionStorage.getItem(LAST_TEST_RESULT_KEY);
    return raw ? (JSON.parse(raw) as TestFinishResult) : null;
  } catch {
    return null;
  }
}

function NodesCard({ result }: { result: TestFinishResult }): ReactNode {
  return (
    <SectionCard
      title="Узлы последней сессии"
      extra={
        result.integrity ? (
          <IntegrityBadge level={result.integrity.level} flags={result.integrity.flags} />
        ) : undefined
      }
    >
      <ul className="grid gap-2">
        {result.nodes.map((node) => (
          <li
            key={node.code}
            className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border px-3 py-2"
          >
            <span className="flex min-w-0 items-center gap-2">
              <span className="shrink-0 text-xs font-semibold text-muted-foreground">
                {node.code}
              </span>
              <span className="truncate text-sm font-medium">{node.title}</span>
            </span>
            <span className="flex shrink-0 items-center gap-2">
              <span className="text-xs text-muted-foreground">
                балл {Math.round(node.score * 100)}%
              </span>
              <StatusBadge size="sm" status={NODE_STATE_STATUS[node.state]}>
                {nodeStateLabel(node.state)}
              </StatusBadge>
            </span>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-xs leading-relaxed text-muted-foreground">
        Подтверждения со временем затухают: «затухает» и «устарел» обновляются регулярным
        заданием или повторным тестом.
      </p>
    </SectionCard>
  );
}

// ---------------------------------------------------------------------------
// Разбор последнего теста (GET /assessment/sessions/last/review)
// ---------------------------------------------------------------------------

/** Итог и узлы последней сессии: заголовочная карточка разбора. */
function ReviewSummaryCard({
  review,
  specialization,
}: {
  review: TestReviewOut;
  specialization: string | null;
}): ReactNode {
  const spec = review.specialization ?? specialization;
  return (
    <SectionCard
      title="Разбор последнего теста"
      extra={
        <StatusBadge status={OUTCOME_STATUS[review.outcome]}>
          {testOutcomeLabel(review.outcome)}
        </StatusBadge>
      }
    >
      <p className="text-sm text-muted-foreground">
        Цель — грейд {gradeLabel(review.target_grade)}
        {spec ? <> · направление «{specializationLabel(spec)}»</> : null}
        {review.mode ? <> · режим «{TEST_MODE_LABELS[review.mode] ?? review.mode}»</> : null}
        {' · завершён '}
        {formatDateTime(review.finished_at)}
      </p>
      <ul className="mt-3 grid gap-2">
        {review.nodes.map((node) => (
          <li
            key={node.code}
            className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border px-3 py-2"
          >
            <span className="flex min-w-0 items-center gap-2">
              <span className="shrink-0 text-xs font-semibold text-muted-foreground">
                {node.code}
              </span>
              <span className="truncate text-sm font-medium">{node.title}</span>
            </span>
            <span className="flex shrink-0 items-center gap-2">
              <span className="text-xs text-muted-foreground">
                балл {Math.round(node.score * 100)}%
              </span>
              <StatusBadge size="sm" status={NODE_STATE_STATUS[node.state]}>
                {nodeStateLabel(node.state)}
              </StatusBadge>
            </span>
          </li>
        ))}
      </ul>
    </SectionCard>
  );
}

/** Строка критерия рубрики: иконка по hit (1 / 0.5 / 0), текст и обоснование судьи. */
function CriterionRow({
  criterion,
}: {
  criterion: TestReviewItem['criteria'][number];
}): ReactNode {
  const Icon = criterion.hit >= 1 ? CheckCircle2 : criterion.hit > 0 ? CircleDot : XCircle;
  const iconClass =
    criterion.hit >= 1
      ? 'text-status-success-deep'
      : criterion.hit > 0
        ? 'text-status-warning-deep'
        : 'text-status-danger-deep';
  return (
    <li className="flex items-start gap-2 text-sm leading-relaxed">
      <Icon className={`mt-0.5 size-4 shrink-0 ${iconClass}`} aria-hidden="true" />
      <span className="min-w-0">
        {criterion.text}
        {criterion.hit < 1 && criterion.evidence ? (
          <em className="mt-0.5 block text-xs text-muted-foreground">{criterion.evidence}</em>
        ) : null}
      </span>
    </li>
  );
}

/** Карточка задания в разборе: итог по виду задания, критерии рубрики и совет. */
function ReviewItemRow({ item }: { item: TestReviewItem }): ReactNode {
  return (
    <li className="rounded-md border border-border p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-2">
          <span className="shrink-0 text-xs font-semibold text-muted-foreground">
            {item.node_code}
          </span>
          <span className="min-w-0 text-sm font-medium">
            {item.title}
            <span className="block truncate text-xs font-normal text-muted-foreground">
              узел «{item.node_title}»
            </span>
          </span>
        </span>
        <span className="flex shrink-0 items-center gap-2">
          {item.llm_fallback ? (
            <span className="text-xs text-muted-foreground">оценено эвристикой</span>
          ) : null}
          <span className="text-xs text-muted-foreground">
            балл {Math.round(item.score * 100)}%
          </span>
        </span>
      </div>

      {item.kind === 'choice' ? (
        <p className="mt-2 flex items-center gap-1.5 text-sm">
          {item.correct ? (
            <>
              <CheckCircle2
                className="size-4 shrink-0 text-status-success-deep"
                aria-hidden="true"
              />
              Ответ верный
            </>
          ) : (
            <>
              <XCircle className="size-4 shrink-0 text-status-danger-deep" aria-hidden="true" />
              Ответ неверный
            </>
          )}
        </p>
      ) : null}

      {item.kind === 'skipped' ? (
        <p className="mt-2 text-sm text-muted-foreground">Без ответа</p>
      ) : null}

      {item.kind === 'text' && item.criteria.length ? (
        <ul className="mt-3 grid gap-1.5">
          {item.criteria.map((criterion) => (
            <CriterionRow key={criterion.id} criterion={criterion} />
          ))}
        </ul>
      ) : null}

      {item.feedback_short ? (
        <p className="mt-3 text-xs leading-relaxed">
          <span className="font-semibold">Что улучшить:</span>{' '}
          <span className="text-muted-foreground">{item.feedback_short}</span>
        </p>
      ) : null}
    </li>
  );
}

function ReviewItemsCard({ review }: { review: TestReviewOut }): ReactNode {
  if (!review.items.length) return null;
  return (
    <SectionCard title="Задания и критерии оценки">
      <ul className="grid gap-3">
        {review.items.map((item, index) => (
          // Стабильного id задания в контракте нет — индекс безопасен (список статичен)
          // eslint-disable-next-line react/no-array-index-key
          <ReviewItemRow key={`${item.node_code}-${index}`} item={item} />
        ))}
      </ul>
    </SectionCard>
  );
}

/** Совет по слабому узлу: feedback_short первого слабого задания этого узла. */
function weakNodeAdvice(review: TestReviewOut, nodeCode: string): string | null {
  const nodeItems = review.items.filter((item) => item.node_code === nodeCode);
  const weak =
    nodeItems.find(
      (item) => item.score < 0.7 || !item.answered || item.correct === false,
    ) ?? nodeItems.find((item) => Boolean(item.feedback_short));
  return weak?.feedback_short ?? null;
}

/** «Что подтянуть до грейда X»: узлы, не подтверждённые или с баллом ниже 70%. */
function ReviewImproveCard({ review }: { review: TestReviewOut }): ReactNode {
  const weakNodes = review.nodes.filter((node) => node.state !== 'fresh' || node.score < 0.7);
  if (!weakNodes.length) return null;
  return (
    <SectionCard title={`Что подтянуть до грейда ${gradeLabel(review.target_grade)}`}>
      <ul className="grid gap-2">
        {weakNodes.map((node) => {
          const advice = weakNodeAdvice(review, node.code);
          return (
            <li key={node.code} className="text-sm leading-relaxed">
              <span className="font-medium">{node.title}</span>
              {advice ? <span className="text-muted-foreground"> — {advice}</span> : null}
            </li>
          );
        })}
      </ul>
      <p className="mt-3 text-xs leading-relaxed text-muted-foreground">
        Подтверждённые узлы сохраняются: на повторном тесте проверятся только слабые места.
      </p>
    </SectionCard>
  );
}

// ---------------------------------------------------------------------------
// Разбор от ИИ-консультанта (GET /candidates/me/graph-summary)
// ---------------------------------------------------------------------------

/** «Разбор от ИИ-консультанта»: граф кандидата человеческим языком. Запрос уходит ТОЛЬКО
 * по клику (бережём дневной лимит GigaChat), дальше ответ кэшируется бэком; генерация LLM
 * может идти до пары минут. 404 — данных графа ещё нет; `source === 'rules'` — упрощённый
 * разбор без ИИ (бейдж-пометка). */
function GraphAiSummaryCard(): ReactNode {
  const summaryQuery = useQuery({
    queryKey: ['candidates', 'graph-summary'],
    queryFn: ({ signal }) => fetchMyGraphSummary(signal),
    enabled: false,
    retry: false,
    staleTime: Infinity,
  });

  const summary = summaryQuery.data ?? null;
  // 404 — штатно: граф ещё не наполнен (теста не было)
  const noData =
    summaryQuery.isError &&
    isApiError(summaryQuery.error) &&
    summaryQuery.error.status === 404;

  return (
    <SectionCard
      title="Разбор от ИИ-консультанта"
      extra={
        summary?.source === 'rules' ? (
          <StatusBadge size="sm">упрощённый разбор без ИИ</StatusBadge>
        ) : undefined
      }
    >
      {summaryQuery.isFetching ? (
        <div className="flex items-center gap-3 py-1">
          <Loader2 className="size-5 shrink-0 animate-spin text-primary" aria-hidden="true" />
          <p className="text-sm leading-relaxed text-muted-foreground">
            ИИ-консультант разбирает ваш граф — до пары минут.
          </p>
        </div>
      ) : summary ? (
        <>
          <div className="grid gap-2">
            {summary.summary.split('\n\n').map((paragraph, index) => (
              // Абзацы саммари статичны после загрузки — индекс безопасен
              // eslint-disable-next-line react/no-array-index-key
              <p key={index} className="text-sm leading-relaxed">
                {paragraph}
              </p>
            ))}
          </div>
          <p className="mt-3 text-xs text-muted-foreground">
            Сгенерировано {formatDateTime(summary.generated_at)}
          </p>
        </>
      ) : noData ? (
        <p className="text-sm leading-relaxed text-muted-foreground">
          Данных графа пока нет — разбор появится после первого пройденного теста.
        </p>
      ) : summaryQuery.isError ? (
        <>
          <p className="text-sm leading-relaxed text-muted-foreground">
            Не получилось подготовить разбор. Попробуйте ещё раз — обычно помогает.
          </p>
          <Button
            variant="outline"
            size="sm"
            className="mt-3"
            onClick={() => void summaryQuery.refetch()}
          >
            Попробовать ещё раз
          </Button>
        </>
      ) : (
        <>
          <p className="text-sm leading-relaxed text-muted-foreground">
            ИИ-консультант объяснит ваш граф человеческим языком: что уже подтверждено, что
            затухает и куда двигаться дальше.
          </p>
          <Button
            variant="brand"
            size="sm"
            className="mt-3"
            onClick={() => void summaryQuery.refetch()}
          >
            <Sparkles aria-hidden="true" />
            Объяснить мой граф
          </Button>
        </>
      )}
    </SectionCard>
  );
}

export function CandidateCategoryPage(): ReactNode {
  const location = useLocation();
  const lastResult = readLastResult(location.state);

  const categoryQuery = useQuery({
    queryKey: ['candidates', 'category'],
    queryFn: ({ signal }) => fetchMyCategory(signal),
    retry: false,
  });

  // Разбор последней завершённой сессии; 404 (теста ещё не было) — секции просто нет
  const reviewQuery = useQuery({
    queryKey: LAST_REVIEW_QUERY_KEY,
    queryFn: ({ signal }) => fetchLastTestReview(signal),
    retry: false,
    refetchOnWindowFocus: false,
  });
  const review = reviewQuery.data ?? null;

  // 404 category_not_assigned — штатно: категории ещё нет
  const notAssigned =
    categoryQuery.isError &&
    isApiError(categoryQuery.error) &&
    categoryQuery.error.status === 404;

  if (categoryQuery.isPending) return <LoadingState rows={6} />;
  if (categoryQuery.isError && !notAssigned) {
    return (
      <ErrorState error={categoryQuery.error} onRetry={() => void categoryQuery.refetch()} />
    );
  }
  const category = categoryQuery.data ?? null;

  if (!category) {
    const testFailed =
      review?.outcome === 'failed' || (lastResult ? !lastResult.grade_confirmed : false);
    return (
      <>
        <PageHeader
          title="Категория и грейд"
          subtitle="Специализация × грейд, уровень связности Q, свежесть подтверждений"
        />
        {testFailed ? (
          <Alert variant="warning" className="mb-4">
            <AlertTitle>Тест не пройден</AlertTitle>
            <AlertDescription>
              Не хватило активных узлов на целевой грейд. Уже подтверждённые узлы сохранены —
              разберите ошибки ниже, попробуйте грейд ниже или повторите позже.
            </AlertDescription>
          </Alert>
        ) : null}
        {review ? (
          <div className="grid gap-4">
            <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
              <ReviewSummaryCard review={review} specialization={null} />
              <SectionCard title="Что дальше">
                <p className="text-sm leading-relaxed text-muted-foreground">
                  До присвоения категории профиль не виден работодателям. Пройдите тест —
                  категория присваивается только по его результатам.
                </p>
                <Button variant="brand" size="sm" className="mt-3" asChild>
                  <Link to="/candidate/test">Пройти тест</Link>
                </Button>
              </SectionCard>
            </div>
            <ReviewImproveCard review={review} />
            <ReviewItemsCard review={review} />
          </div>
        ) : testFailed && lastResult ? (
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
            <NodesCard result={lastResult} />
            <SectionCard title="Что дальше">
              <p className="text-sm leading-relaxed text-muted-foreground">
                До присвоения категории профиль не виден работодателям. Пройдите тест —
                категория присваивается только по его результатам.
              </p>
              <Button variant="brand" size="sm" className="mt-3" asChild>
                <Link to="/candidate/test">Пройти тест</Link>
              </Button>
            </SectionCard>
          </div>
        ) : (
          <section className="rounded-lg border border-border bg-card shadow-card">
            <div className="mx-auto flex max-w-[520px] flex-col items-center gap-3 px-6 py-12 text-center">
              <Illustration name="path-empty" height={140} />
              <p className="text-base font-semibold">Категории пока нет</p>
              <p className="text-sm leading-relaxed text-muted-foreground">
                Категория присваивается только по результатам теста: заполните профиль, выберите
                целевой грейд и пройдите проверку узлов графа.
              </p>
              <Button variant="brand" className="mt-1" asChild>
                <Link to="/candidate/test">Пройти тест</Link>
              </Button>
            </div>
          </section>
        )}
      </>
    );
  }

  const specTitle = specializationLabel(category.specialization);
  const gradeTitle = gradeLabel(category.grade);

  return (
    <>
      <PageHeader
        title="Категория и грейд"
        subtitle={`Присвоена ${formatDateTime(category.computed_at)} по результатам теста`}
        meta={
          category.grade_confirmed ? (
            <StatusBadge status="success" icon={BadgeCheck}>
              Подтверждена тестом
            </StatusBadge>
          ) : (
            <StatusBadge status="warning">Заявлена, не подтверждена</StatusBadge>
          )
        }
        extra={
          <>
            <Button variant="outline" asChild>
              <Link to={`/skills/graph/${category.specialization}`}>
                <Share2 aria-hidden="true" />
                Граф навыков
              </Link>
            </Button>
            <Button variant="brand" asChild>
              <Link to="/candidate/invitations">
                <Mail aria-hidden="true" />
                К приглашениям
              </Link>
            </Button>
          </>
        }
      />

      <div className="grid gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
        <SectionCard title="Категория">
          <p className="text-2xl font-semibold tracking-tight">
            {specTitle} · {gradeTitle}
          </p>
          <p className="mt-1 text-sm text-muted-foreground">ИТ · {specTitle.toLowerCase()}</p>
          <div className="mt-4 rounded-md bg-muted/50 p-3">
            <p className="text-xs text-muted-foreground">Уровень по графу (связность)</p>
            <p className="mt-0.5 text-xl font-semibold">Q = {category.q_level}</p>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
              Размер остовного леса активных узлов: чем связнее подтверждённые навыки, тем выше Q.
            </p>
          </div>
        </SectionCard>

        {review ? (
          <ReviewSummaryCard review={review} specialization={category.specialization} />
        ) : lastResult ? (
          <NodesCard result={lastResult} />
        ) : (
          <SectionCard title="Узлы последней сессии">
            <p className="text-sm leading-relaxed text-muted-foreground">
              Разбор по узлам показывается сразу после прохождения теста. Актуальное состояние
              навыков — на графе специализации.
            </p>
          </SectionCard>
        )}
      </div>

      <div className="mt-4 grid gap-4">
        <GraphAiSummaryCard />
        {review ? (
          <>
            <ReviewImproveCard review={review} />
            <ReviewItemsCard review={review} />
          </>
        ) : null}
      </div>
    </>
  );
}
