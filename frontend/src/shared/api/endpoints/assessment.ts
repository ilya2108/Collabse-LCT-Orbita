import { api } from '../client';
import type {
  IntegrityEvent,
  IntegrityEventsAccepted,
  IntegrityReport,
  TargetGrade,
  TestAnswerBody,
  TestAnswerResult,
  TestFinishResult,
  TestItemCurrent,
  TestMode,
  TestReviewOut,
  TestSessionOut,
} from '../types';

/**
 * Эндпоинты сессий теста на грейд и сигналов целостности (контракт интегратора 11.10).
 */

/** Создать сессию (201) или получить уже активную (200 с ней же). `mode`: express —
 * до 5 заданий (демо-профиль), full — все узлы профиля грейда; без него решает стенд.
 * 409: profile_incomplete | cooldown_active (details[0].message — ISO-дата) | no_templates_available. */
export function createTestSession(
  targetGrade: TargetGrade,
  mode?: TestMode,
): Promise<TestSessionOut> {
  return api.post<TestSessionOut>('/assessment/sessions', {
    body: { target_grade: targetGrade, ...(mode ? { mode } : {}) },
  });
}

/** Активная сессия или 404. */
export function fetchCurrentTestSession(signal?: AbortSignal): Promise<TestSessionOut> {
  return api.get<TestSessionOut>('/assessment/sessions/current', { signal });
}

/** Текущее задание сессии (без эталонов и рубрик); первый показ запускает серверный таймер. */
export function fetchCurrentTestItem(
  sessionId: string,
  signal?: AbortSignal,
): Promise<TestItemCurrent> {
  return api.get<TestItemCurrent>(
    `/assessment/sessions/${encodeURIComponent(sessionId)}/items/current`,
    { signal },
  );
}

/** Сохранить ответ; повторный сабмит того же задания → 409 item_already_answered. */
export function submitTestAnswer(
  sessionId: string,
  itemId: string,
  body: TestAnswerBody,
): Promise<TestAnswerResult> {
  return api.post<TestAnswerResult>(
    `/assessment/sessions/${encodeURIComponent(sessionId)}/items/${encodeURIComponent(itemId)}/answer`,
    { body },
  );
}

/** Завершить сессию: грейдинг внутри (LLM — может занять минуту-две, таймаутов на клиенте нет).
 * Идемпотентен: по уже завершённой сессии — 200 с тем же итогом;
 * пока оценка выполняется — 409 grading_in_progress (клиент повторяет запрос сам). */
export function finishTestSession(sessionId: string): Promise<TestFinishResult> {
  return api.post<TestFinishResult>(
    `/assessment/sessions/${encodeURIComponent(sessionId)}/finish`,
  );
}

/** Ключ React Query разбора последней сессии — общий для страниц «Тест» и «Категория». */
export const LAST_REVIEW_QUERY_KEY = ['assessment', 'last-review'] as const;

/** Разбор последней завершённой сессии: узлы, задания и критерии рубрики.
 * 404 — завершённых сессий ещё нет (на UI секция разбора просто не показывается). */
export function fetchLastTestReview(signal?: AbortSignal): Promise<TestReviewOut> {
  return api.get<TestReviewOut>('/assessment/sessions/last/review', { signal });
}

/** Батч телеметрии раннера → 202 {accepted}. Авторизация: кандидат-владелец сессии. */
export function sendIntegrityEvents(
  sessionId: string,
  events: IntegrityEvent[],
): Promise<IntegrityEventsAccepted> {
  return api.post<IntegrityEventsAccepted>(
    `/assessment/sessions/${encodeURIComponent(sessionId)}/events`,
    { body: { events } },
  );
}

/** Индекс целостности сессии: владелец-кандидат или admin. */
export function fetchSessionIntegrity(
  sessionId: string,
  signal?: AbortSignal,
): Promise<IntegrityReport> {
  return api.get<IntegrityReport>(
    `/assessment/sessions/${encodeURIComponent(sessionId)}/integrity`,
    { signal },
  );
}
