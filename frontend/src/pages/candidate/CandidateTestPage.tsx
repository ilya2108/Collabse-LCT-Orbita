import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowRight, Clock, Loader2, Play, RotateCcw } from 'lucide-react';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  createTestSession,
  fetchCurrentTestItem,
  fetchCurrentTestSession,
  fetchLastTestReview,
  finishTestSession,
  LAST_REVIEW_QUERY_KEY,
  submitTestAnswer,
} from '@/shared/api/endpoints/assessment';
import { fetchMyProfile } from '@/shared/api/endpoints/candidates';
import { isApiError } from '@/shared/api/errors';
import { notifyApiError } from '@/shared/api/feedback';
import { gradeLabel, specializationLabel } from '@/shared/config/labels';
import type {
  TargetGrade,
  TestFinishResult,
  TestItemBody,
  TestMode,
  TestSessionOut,
} from '@/shared/api/types';
import { useIntegrityTelemetry } from '@/shared/telemetry/useIntegrityTelemetry';
import { formatDateTime } from '@/shared/lib/format';
import { Alert, AlertDescription, AlertTitle } from '@/shared/ui/alert';
import { Button } from '@/shared/ui/button';
import { Label } from '@/shared/ui/label';
import { PageHeader } from '@/shared/ui/page-header';
import { PreTestNotice } from '@/shared/ui/pre-test-notice';
import { Progress } from '@/shared/ui/progress';
import { RadioGroup, RadioGroupItem } from '@/shared/ui/radio-group';
import { ErrorState, LoadingState } from '@/shared/ui/states';
import { StatusBadge } from '@/shared/ui/status-badge';
import { Textarea } from '@/shared/ui/textarea';

/**
 * «Тест» — путь кандидата к категории (контракт интегратора 11.10):
 * старт (выбор целевого грейда + баннер прозрачности) → раннер (одно задание на экран,
 * мягкий таймер, телеметрия целостности) → «Проверяем ответы…» (finish: грейдинг LLM,
 * до минуты-двух) → переход на «Категорию и грейд» с результатом.
 */

/** Ключ sessionStorage с итогом последнего finish — его читает страница «Категория». */
export const LAST_TEST_RESULT_KEY = 'fsp.lastTestResult';

const GRADE_OPTIONS: Array<{ value: TargetGrade; label: string; hint: string }> = [
  { value: 'junior', label: 'Junior', hint: 'База специализации: применение по образцу' },
  { value: 'middle', label: 'Middle', hint: 'Анализ и проектирование с компромиссами' },
  { value: 'senior', label: 'Senior', hint: 'Неочевидные случаи и цена решения' },
];

const MODE_OPTIONS: Array<{ value: TestMode; label: string; hint: string }> = [
  { value: 'express', label: 'Экспресс', hint: 'До 5 заданий, быстрое знакомство' },
  {
    value: 'full',
    label: 'Полный',
    hint: 'Все узлы профиля грейда, покажет систему в полную силу',
  },
];

const MODE_BADGES: Record<TestMode, string> = {
  express: 'Экспресс-режим',
  full: 'Полный режим',
};

/** Инлайн-markdown: `код` и **жирный**; остальное — как есть (шрифт и так моноширинный). */
function InlineMd({ text }: { text: string }): ReactNode {
  return text.split(/(`[^`\n]+`|\*\*[^*\n]+\*\*)/g).map((token, index) => {
    if (token.startsWith('`') && token.endsWith('`')) {
      return (
        // eslint-disable-next-line react/no-array-index-key
        <code key={index} className="rounded bg-muted px-1 py-0.5 text-[0.9em] font-semibold">
          {token.slice(1, -1)}
        </code>
      );
    }
    if (token.startsWith('**') && token.endsWith('**')) {
      // eslint-disable-next-line react/no-array-index-key
      return <strong key={index}>{token.slice(2, -2)}</strong>;
    }
    return token;
  });
}

const LIST_MARKER = /^(?:[-*]|\d+[.)])\s+/;

/**
 * Блок между пустыми строками: список (маркеры «-», «*», «1.») или абзац. Тела заданий в YAML —
 * literal-блоки с жёсткой шириной строки, поэтому одиночный \n — мягкий перенос (склеиваем
 * пробелом по правилам markdown), иначе текст рвётся посреди предложения.
 */
function MdBlock({ block }: { block: string }): ReactNode {
  const lines = block.split('\n').map((line) => line.trim());
  if (lines.some((line) => LIST_MARKER.test(line))) {
    const items: string[] = [];
    for (const line of lines) {
      if (LIST_MARKER.test(line) || items.length === 0) {
        items.push(line.replace(LIST_MARKER, ''));
      } else {
        items[items.length - 1] += ` ${line}`;
      }
    }
    const ordered = /^\d/.test(lines.find((line) => LIST_MARKER.test(line)) ?? '');
    const ListTag = ordered ? 'ol' : 'ul';
    return (
      <ListTag
        className={`grid gap-1 pl-5 text-sm leading-relaxed ${ordered ? 'list-decimal' : 'list-disc'}`}
      >
        {items.map((item, index) => (
          // eslint-disable-next-line react/no-array-index-key
          <li key={index}>
            <InlineMd text={item} />
          </li>
        ))}
      </ListTag>
    );
  }
  return (
    <p className="text-sm leading-relaxed">
      <InlineMd text={lines.join(' ')} />
    </p>
  );
}

/** Рендер markdown-тела без зависимостей: ```-блоки кода, абзацы, списки, инлайн-код. */
function ItemBodyMd({ body }: { body: string }): ReactNode {
  const parts = body.split(/```[a-z]*\n?/);
  return (
    <div className="grid gap-3">
      {parts.map((part, index) =>
        index % 2 === 1 ? (
          // eslint-disable-next-line react/no-array-index-key
          <pre key={index} className="overflow-x-auto rounded-md bg-muted p-3 text-xs leading-relaxed">
            {part.replace(/\n$/, '')}
          </pre>
        ) : part.trim() ? (
          part
            .trim()
            .split(/\n{2,}/)
            // eslint-disable-next-line react/no-array-index-key
            .map((block, blockIndex) => <MdBlock key={`${index}-${blockIndex}`} block={block} />)
        ) : null,
      )}
    </div>
  );
}

function formatSeconds(total: number): string {
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

/** Мягкий таймер задания: по нулю только пометка «время вышло», ответ не блокируется. */
function ItemTimer({ itemId, limit }: { itemId: string; limit: number }): ReactNode {
  const [left, setLeft] = useState(limit);
  useEffect(() => {
    setLeft(limit);
    const timer = window.setInterval(() => setLeft((prev) => Math.max(prev - 1, 0)), 1000);
    return () => window.clearInterval(timer);
  }, [itemId, limit]);
  if (left === 0) {
    return (
      <StatusBadge status="warning" icon={Clock}>
        Время вышло — ответ всё равно будет принят
      </StatusBadge>
    );
  }
  return (
    <StatusBadge status={left <= 15 ? 'warning' : 'progress'} icon={Clock}>
      {formatSeconds(left)}
    </StatusBadge>
  );
}

// ---------------------------------------------------------------------------
// Экран «Проверяем ответы…»
// ---------------------------------------------------------------------------

function GradingScreen({ stalled = false }: { stalled?: boolean }): ReactNode {
  return (
    <section className="rounded-lg border border-border bg-card shadow-card">
      <div className="mx-auto flex max-w-[480px] flex-col items-center gap-3 px-6 py-16 text-center">
        <Loader2 className="size-8 animate-spin text-primary" aria-hidden="true" />
        <p className="text-base font-semibold">Проверяем ответы…</p>
        <p className="text-sm leading-relaxed text-muted-foreground">
          Закрытые задания сверяются с ключом, открытые оценивает ИИ-судья по рубрике — это
          может занять минуту-две. Не закрывайте страницу: по готовности покажем категорию
          и разбор по узлам графа.
        </p>
        {stalled ? (
          <p className="text-sm font-medium text-foreground">
            Оценка занимает дольше обычного — не закрывайте страницу.
          </p>
        ) : null}
      </div>
    </section>
  );
}

/** Карточка после исчерпания повторов finish: ответы на сервере, можно продолжить вручную. */
function FinishRetryCard({ onRetry }: { onRetry: () => void }): ReactNode {
  return (
    <section className="rounded-lg border border-border bg-card shadow-card">
      <div className="mx-auto flex max-w-[480px] flex-col items-center gap-3 px-6 py-16 text-center">
        <p className="text-base font-semibold">Не получилось завершить проверку</p>
        <p className="text-sm leading-relaxed text-muted-foreground">
          Ваши ответы сохранены на сервере и никуда не пропадут. Проверьте соединение и
          повторите — оценка продолжится с того же места.
        </p>
        <Button variant="brand" onClick={onRetry}>
          <RotateCcw aria-hidden="true" />
          Попробовать ещё раз
        </Button>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Раннер: одно задание на экран
// ---------------------------------------------------------------------------

/** Пауза между повторами finish и лимит повторов (~5 минут суммарно). */
const FINISH_RETRY_DELAY_MS = 8_000;
const FINISH_RETRY_MAX_ATTEMPTS = 37;

function TestRunner({ session }: { session: TestSessionOut }): ReactNode {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const telemetry = useIntegrityTelemetry(session.id);
  const [choiceId, setChoiceId] = useState<string | null>(null);
  const [text, setText] = useState('');
  // Ожидание долгого грейдинга: показываем мягкий текст и повторяем finish по таймеру
  const [finishStalled, setFinishStalled] = useState(false);
  // Повторы исчерпаны — карточка с кнопкой «Попробовать ещё раз»
  const [finishExhausted, setFinishExhausted] = useState(false);
  const finishAttemptsRef = useRef(0);
  const finishTimerRef = useRef<number | null>(null);

  // Сброс таймера повторов при размонтировании (уход со страницы)
  useEffect(
    () => () => {
      if (finishTimerRef.current !== null) window.clearTimeout(finishTimerRef.current);
    },
    [],
  );

  const itemQuery = useQuery({
    queryKey: ['assessment', 'item', session.id],
    queryFn: ({ signal }) => fetchCurrentTestItem(session.id, signal),
    retry: false,
    refetchOnWindowFocus: false,
  });
  const item: TestItemBody | null = itemQuery.data?.item ?? null;

  // Показ задания — точка отсчёта таймингов телеметрии (повтор по id исключён эффектом)
  useEffect(() => {
    if (item) telemetry.itemShown(item.id);
  }, [item?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  /** Сессия уже завершена (другая вкладка/повторный finish): забираем разбор и уходим. */
  const goToReview = async (): Promise<void> => {
    telemetry.flush();
    void queryClient.invalidateQueries({ queryKey: ['candidates'] });
    void queryClient.invalidateQueries({ queryKey: ['assessment'] });
    try {
      const review = await fetchLastTestReview();
      queryClient.setQueryData(LAST_REVIEW_QUERY_KEY, review);
    } catch {
      // Разбора нет — «Категория» покажет данные из GET /candidates/me/category
    }
    navigate('/candidate/category');
  };

  const finishMutation = useMutation({
    mutationFn: () => finishTestSession(session.id),
    onSuccess: (result: TestFinishResult) => {
      telemetry.flush();
      try {
        sessionStorage.setItem(LAST_TEST_RESULT_KEY, JSON.stringify(result));
      } catch {
        // sessionStorage недоступен — категория покажет данные из GET /candidates/me/category
      }
      void queryClient.invalidateQueries({ queryKey: ['candidates'] });
      void queryClient.invalidateQueries({ queryKey: ['assessment'] });
      navigate('/candidate/category', { state: { finish: result } });
    },
    onError: (error) => {
      // Бэкенд теперь идемпотентен и session_not_active не шлёт, но перестраховываемся:
      // сессия уже завершена — результат есть, достаём разбор и уходим на «Категорию»
      if (isApiError(error) && error.code === 'session_not_active') {
        void goToReview();
        return;
      }
      // Оценка ещё идёт или связь моргнула — это не ошибка кандидата: остаёмся на экране
      // «Проверяем ответы…» и молча повторяем finish по таймеру
      const retriable =
        !isApiError(error) ||
        error.code === 'grading_in_progress' ||
        error.code === 'network_error' ||
        error.status === 0 ||
        error.status === 502 ||
        error.status === 503 ||
        error.status === 504;
      if (retriable && finishAttemptsRef.current < FINISH_RETRY_MAX_ATTEMPTS) {
        finishAttemptsRef.current += 1;
        setFinishStalled(true);
        finishTimerRef.current = window.setTimeout(
          () => finishMutation.mutate(),
          FINISH_RETRY_DELAY_MS,
        );
        return;
      }
      // Повторы исчерпаны или ошибка неожиданная — карточка с ручным повтором, не тупик
      if (!retriable) notifyApiError(error, 'Не удалось завершить тест');
      setFinishStalled(false);
      setFinishExhausted(true);
    },
  });

  /** Ручной повтор после исчерпания автоматических попыток. */
  const retryFinish = (): void => {
    finishAttemptsRef.current = 0;
    setFinishExhausted(false);
    setFinishStalled(true);
    finishMutation.mutate();
  };

  const answerMutation = useMutation({
    mutationFn: (payload: { itemId: string; body: { text?: string; choice_id?: string } }) =>
      submitTestAnswer(session.id, payload.itemId, payload.body),
    onSuccess: (result) => {
      setChoiceId(null);
      setText('');
      if (result.finished) {
        finishMutation.mutate();
        return;
      }
      void itemQuery.refetch();
    },
    onError: (error) => {
      // Повторный сабмит (двойной клик, гонка) — просто берём следующее задание
      if (isApiError(error) && error.code === 'item_already_answered') {
        setChoiceId(null);
        setText('');
        void itemQuery.refetch();
        return;
      }
      notifyApiError(error, 'Не удалось сохранить ответ');
    },
  });

  if (finishExhausted) return <FinishRetryCard onRetry={retryFinish} />;
  if (finishMutation.isPending || finishMutation.isSuccess || finishStalled) {
    return <GradingScreen stalled={finishStalled} />;
  }
  if (itemQuery.isPending) return <LoadingState rows={6} />;
  if (itemQuery.isError) {
    // 404 «все задания отвечены» — сессия добита в другой вкладке: завершаем
    if (isApiError(itemQuery.error) && itemQuery.error.status === 404) {
      return (
        <section className="rounded-lg border border-border bg-card p-6 shadow-card">
          <p className="text-sm text-muted-foreground">
            Все задания уже отвечены — осталось завершить сессию и получить результат.
          </p>
          <Button className="mt-3" variant="brand" onClick={() => finishMutation.mutate()}>
            Завершить и узнать результат
          </Button>
        </section>
      );
    }
    return <ErrorState error={itemQuery.error} onRetry={() => void itemQuery.refetch()} />;
  }
  const current = itemQuery.data;
  if (!current || !item) return null;
  const answerLength = item.kind === 'choice' ? (choiceId?.length ?? 0) : text.trim().length;
  const canSubmit =
    !answerMutation.isPending &&
    (item.kind === 'choice' ? choiceId !== null : text.trim().length > 0);

  const submit = (): void => {
    if (!canSubmit) return;
    telemetry.answerSubmitted(item.id, answerLength);
    answerMutation.mutate({
      itemId: item.id,
      body: item.kind === 'choice' ? { choice_id: choiceId ?? '' } : { text: text.trim() },
    });
  };

  return (
    <section className="mx-auto w-full max-w-[760px]">
      <div className="mb-3 flex items-center gap-3">
        <Progress
          value={(current.index / current.total) * 100}
          aria-label="Прогресс теста"
          className="flex-1"
        />
        <span className="shrink-0 text-sm text-muted-foreground">
          Задание {current.index + 1} из {current.total}
        </span>
      </div>

      <div className="rounded-lg border border-border bg-card p-5 shadow-card">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <StatusBadge status="brand">Узел {item.node_code}</StatusBadge>
            <h2 className="text-base font-semibold">{item.title}</h2>
          </div>
          {item.time_limit_seconds ? (
            <ItemTimer itemId={item.id} limit={item.time_limit_seconds} />
          ) : null}
        </div>

        <div className="mt-4">
          <ItemBodyMd body={item.body_md} />
        </div>

        <div className="mt-5">
          {item.kind === 'choice' && item.options ? (
            <RadioGroup
              value={choiceId ?? ''}
              onValueChange={(value) => {
                telemetry.firstInput(item.id);
                setChoiceId(value);
              }}
              className="grid gap-2"
            >
              {item.options.map((option) => (
                <label
                  key={option.id}
                  htmlFor={`option-${item.id}-${option.id}`}
                  className="flex cursor-pointer items-start gap-3 rounded-md border border-border p-3 text-sm leading-relaxed transition-colors hover:bg-muted/50 has-[[data-state=checked]]:border-primary has-[[data-state=checked]]:bg-primary-tint"
                >
                  <RadioGroupItem
                    id={`option-${item.id}-${option.id}`}
                    value={option.id}
                    className="mt-0.5"
                  />
                  <span>
                    <span className="mr-1.5 font-semibold">{option.id}.</span>
                    <InlineMd text={option.text} />
                  </span>
                </label>
              ))}
            </RadioGroup>
          ) : (
            <div className="grid gap-1.5">
              <Label htmlFor={`answer-${item.id}`}>Ваш ответ</Label>
              <Textarea
                id={`answer-${item.id}`}
                rows={7}
                maxLength={3000}
                value={text}
                onChange={(e) => {
                  telemetry.firstInput(item.id);
                  setText(e.target.value);
                }}
                placeholder="Механизм проблемы и варианты решения с их ценой…"
              />
              <p className="text-xs text-muted-foreground">
                Оценивается содержание по рубрике, а не стиль. Не указывайте персональные данные.
              </p>
            </div>
          )}
        </div>

        <div className="mt-5 flex items-center justify-end gap-3 border-t border-border pt-4">
          <Button variant="brand" disabled={!canSubmit} onClick={submit}>
            {answerMutation.isPending ? 'Сохраняем…' : 'Ответить и дальше'}
            <ArrowRight aria-hidden="true" />
          </Button>
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Стартовый экран: целевой грейд + баннер прозрачности
// ---------------------------------------------------------------------------

function StartScreen({ onStarted }: { onStarted: (session: TestSessionOut) => void }): ReactNode {
  const [grade, setGrade] = useState<TargetGrade>('junior');
  const [mode, setMode] = useState<TestMode>('express');
  const [cooldownUntil, setCooldownUntil] = useState<string | null>(null);

  const profileQuery = useQuery({
    queryKey: ['candidates', 'me'],
    queryFn: ({ signal }) => fetchMyProfile(signal),
    retry: false,
  });
  const profile = profileQuery.data ?? null;
  const profileLoaded = !profileQuery.isPending;
  const noSpecialization = profileLoaded && !profile?.specialization;

  const createMutation = useMutation({
    mutationFn: () => createTestSession(grade, mode),
    onSuccess: onStarted,
    onError: (error) => {
      if (isApiError(error) && error.code === 'cooldown_active') {
        setCooldownUntil(error.details[0]?.message ?? null);
        return;
      }
      notifyApiError(error, 'Не удалось начать тест');
    },
  });

  return (
    <section className="mx-auto grid w-full max-w-[640px] gap-4">
      {noSpecialization ? (
        <Alert variant="warning">
          <AlertTitle>Сначала выберите специализацию</AlertTitle>
          <AlertDescription>
            <p>
              Задания собираются по графу специализации — без неё тест не начать
              (профиль не заполнен).
            </p>
            <Button variant="outline" size="sm" className="mt-2" asChild>
              <Link to="/candidate/profile">Заполнить профиль</Link>
            </Button>
          </AlertDescription>
        </Alert>
      ) : null}

      {cooldownUntil ? (
        <Alert variant="warning">
          <AlertTitle>Повторный тест пока недоступен</AlertTitle>
          <AlertDescription>
            Следующая попытка — с {formatDateTime(cooldownUntil)}. Подтверждённые узлы и
            категория сохраняются.
          </AlertDescription>
        </Alert>
      ) : null}

      <div className="rounded-lg border border-border bg-card p-5 shadow-card">
        <h2 className="text-base font-semibold">Целевой грейд</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          На какой грейд собираем задания. Не прошли — ничего не теряете: уже подтверждённые
          узлы сохранятся, можно попробовать грейд ниже.
        </p>
        <RadioGroup
          value={grade}
          onValueChange={(value) => setGrade(value as TargetGrade)}
          className="mt-4 grid gap-2"
        >
          {GRADE_OPTIONS.map((option) => (
            <label
              key={option.value}
              htmlFor={`grade-${option.value}`}
              className="flex cursor-pointer items-start gap-3 rounded-md border border-border p-3 transition-colors hover:bg-muted/50 has-[[data-state=checked]]:border-primary has-[[data-state=checked]]:bg-primary-tint"
            >
              <RadioGroupItem id={`grade-${option.value}`} value={option.value} className="mt-0.5" />
              <span className="grid gap-0.5">
                <span className="text-sm font-semibold">{option.label}</span>
                <span className="text-xs text-muted-foreground">{option.hint}</span>
              </span>
            </label>
          ))}
        </RadioGroup>
      </div>

      <div className="rounded-lg border border-border bg-card p-5 shadow-card">
        <h2 className="text-base font-semibold">Режим теста</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Экспресс даёт быстрый результат; полный проходит все узлы профиля грейда.
        </p>
        <RadioGroup
          value={mode}
          onValueChange={(value) => setMode(value as TestMode)}
          className="mt-4 grid gap-2 sm:grid-cols-2"
        >
          {MODE_OPTIONS.map((option) => (
            <label
              key={option.value}
              htmlFor={`mode-${option.value}`}
              className="flex cursor-pointer items-start gap-3 rounded-md border border-border p-3 transition-colors hover:bg-muted/50 has-[[data-state=checked]]:border-primary has-[[data-state=checked]]:bg-primary-tint"
            >
              <RadioGroupItem id={`mode-${option.value}`} value={option.value} className="mt-0.5" />
              <span className="grid gap-0.5">
                <span className="text-sm font-semibold">{option.label}</span>
                <span className="text-xs text-muted-foreground">{option.hint}</span>
              </span>
            </label>
          ))}
        </RadioGroup>
      </div>

      <PreTestNotice />

      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">
          {mode === 'express'
            ? 'Одно задание на экране, мягкий таймер. Экспресс — до 5 заданий, 6–10 минут.'
            : 'Одно задание на экране, мягкий таймер. Полный — одно задание на каждый узел профиля.'}
        </p>
        <Button
          variant="brand"
          size="lg"
          disabled={createMutation.isPending || noSpecialization}
          onClick={() => createMutation.mutate()}
        >
          <Play aria-hidden="true" />
          {createMutation.isPending ? 'Собираем задания…' : 'Начать тест'}
        </Button>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Страница
// ---------------------------------------------------------------------------

export function CandidateTestPage(): ReactNode {
  const queryClient = useQueryClient();
  const sessionQuery = useQuery({
    queryKey: ['assessment', 'session', 'current'],
    queryFn: ({ signal }) => fetchCurrentTestSession(signal),
    retry: false,
    refetchOnWindowFocus: false,
  });

  // 404 — активной сессии нет: показываем стартовый экран
  const noSession =
    sessionQuery.isError && isApiError(sessionQuery.error) && sessionQuery.error.status === 404;
  const session = sessionQuery.data ?? null;

  return (
    <>
      <PageHeader
        title="Тест"
        subtitle={
          session
            ? `Цель — грейд ${gradeLabel(session.target_grade)} · направление «${specializationLabel(session.specialization)}»`
            : 'Адаптивная проверка узлов графа: одно задание на экране, ответы оценивает судья с рубрикой'
        }
        meta={
          session?.mode ? (
            <StatusBadge status={session.mode === 'full' ? 'brand' : 'progress'}>
              {MODE_BADGES[session.mode]}
            </StatusBadge>
          ) : null
        }
      />
      {sessionQuery.isPending ? (
        <LoadingState rows={6} />
      ) : session ? (
        <TestRunner key={session.id} session={session} />
      ) : noSession ? (
        <StartScreen
          onStarted={(created) =>
            queryClient.setQueryData(['assessment', 'session', 'current'], created)
          }
        />
      ) : (
        <ErrorState error={sessionQuery.error} onRetry={() => void sessionQuery.refetch()} />
      )}
    </>
  );
}
