import { useCallback, useEffect, useMemo, useRef } from 'react';
import { sendIntegrityEvents } from '@/shared/api/endpoints/assessment';
import type { IntegrityEvent, IntegrityEventKind, IntegrityEventPayload } from '@/shared/api/types';
import { env } from '@/shared/config/env';

/**
 * Телеметрия целостности сессии теста — хук для команды раннера.
 *
 * Автоматически (пока смонтирован компонент с активной сессией):
 * - `paste` — длина вставленного текста (только длина, сам текст НЕ читается в события);
 * - `blur` / `focus` — уход с вкладки и возврат (blur окна и скрытие вкладки считаются
 *   одним отсутствием; в `focus.duration_ms` — сколько длилось отсутствие);
 * - батчинг: отправка POST /assessment/sessions/{id}/events каждые 10 событий или 5 секунд,
 *   на закрытие вкладки (pagehide) — добор через `navigator.sendBeacon`;
 * - отказоустойчивость: ошибки сети глотаются, неотправленные события возвращаются
 *   в очередь, очередь ограничена 200 событиями (старые вытесняются).
 *
 * Через возвращаемые колбэки раннер фиксирует жизненный цикл задания:
 * - `itemShown(itemId)` — задание показано (точка отсчёта таймингов);
 * - `firstInput(itemId)` — первый ввод в поле ответа (дедуплицируется сам);
 * - `answerSubmitted(itemId, answerLength)` — ответ отправлен; передаётся ТОЛЬКО длина
 *   ответа, chars_typed/chars_pasted хук считает сам по событиям paste этого задания.
 *
 * ПДн-минимизация — принципиальна: в события попадают только длины и тайминги.
 * Никогда не передавайте в хук текст ответа — только `answer.length`.
 *
 * Подключение в раннере (3 строки):
 * ```tsx
 * const telemetry = useIntegrityTelemetry(session.id);
 * // при показе задания:            telemetry.itemShown(item.id);
 * // в onChange поля (можно всегда): telemetry.firstInput(item.id);
 * // при отправке ответа:            telemetry.answerSubmitted(item.id, answer.length);
 * ```
 * Перед стартом теста раннер ОБЯЗАН показать баннер прозрачности
 * `PreTestNotice` из `@/shared/ui/pre-test-notice`.
 */

/** Колбэки фиксации жизненного цикла задания; ссылки стабильны между рендерами. */
export interface IntegrityTelemetry {
  /** Задание показано кандидату — точка отсчёта duration_ms. */
  itemShown: (itemId: string) => void;
  /** Первый ввод в поле ответа; повторные вызовы по тому же заданию игнорируются. */
  firstInput: (itemId: string) => void;
  /** Ответ отправлен; `answerLength` — ДЛИНА ответа (`answer.length`), не сам текст. */
  answerSubmitted: (itemId: string, answerLength: number) => void;
  /** Принудительная отправка очереди (например, при завершении теста). */
  flush: () => void;
}

/** Отправляем, когда в очереди накопилось столько событий. */
const FLUSH_COUNT = 10;
/** …или раз в столько миллисекунд. */
const FLUSH_INTERVAL_MS = 5_000;
/** Жёсткий потолок очереди: при недоступной сети старые события вытесняются. */
const MAX_QUEUE = 200;

interface TelemetryState {
  queue: IntegrityEvent[];
  /** Момент ухода с вкладки; null — кандидат на вкладке. */
  awaySince: number | null;
  /** Текущее задание (последний itemShown) — попадает в item_id событий paste. */
  currentItemId: string | null;
  shownAt: Map<string, number>;
  pastedByItem: Map<string, number>;
  firstInputSent: Set<string>;
  /** Суммарно вставлено символов за сессию. */
  totalPasted: number;
  flushing: boolean;
}

function emptyState(): TelemetryState {
  return {
    queue: [],
    awaySince: null,
    currentItemId: null,
    shownAt: new Map(),
    pastedByItem: new Map(),
    firstInputSent: new Set(),
    totalPasted: 0,
    flushing: false,
  };
}

/**
 * Включает сбор телеметрии целостности для сессии теста.
 *
 * @param sessionId id активной сессии; null/undefined — хук полностью пассивен
 *   (удобно, пока сессия ещё создаётся).
 * @param enabled дополнительный выключатель (например, для режима демо без записи).
 */
export function useIntegrityTelemetry(
  sessionId: string | null | undefined,
  enabled = true,
): IntegrityTelemetry {
  const stateRef = useRef<TelemetryState>(emptyState());
  const sessionRef = useRef<string | null>(null);
  sessionRef.current = enabled && sessionId ? sessionId : null;

  /** Отправка очереди; ошибки сети глотаем, события возвращаем в очередь (с потолком). */
  const flush = useCallback((): void => {
    const state = stateRef.current;
    const session = sessionRef.current;
    if (!session || state.flushing || state.queue.length === 0) return;
    const batch = state.queue.splice(0, state.queue.length);
    state.flushing = true;
    sendIntegrityEvents(session, batch)
      .catch(() => {
        // Сеть недоступна — вернём батч в начало очереди, не превышая потолок.
        state.queue = [...batch, ...state.queue].slice(-MAX_QUEUE);
      })
      .finally(() => {
        state.flushing = false;
      });
  }, []);

  const push = useCallback(
    (kind: IntegrityEventKind, payload: IntegrityEventPayload): void => {
      if (!sessionRef.current) return;
      const state = stateRef.current;
      state.queue.push({ kind, client_ts: new Date().toISOString(), payload });
      if (state.queue.length > MAX_QUEUE) state.queue.splice(0, state.queue.length - MAX_QUEUE);
      if (state.queue.length >= FLUSH_COUNT) flush();
    },
    [flush],
  );

  /** Синхронный добор на закрытие вкладки: sendBeacon переживает выгрузку страницы. */
  const flushOnPageHide = useCallback((): void => {
    const state = stateRef.current;
    const session = sessionRef.current;
    if (!session || state.queue.length === 0) return;
    const batch = state.queue.splice(0, state.queue.length);
    if (env.mockApi || typeof navigator.sendBeacon !== 'function') {
      // Фикстуры — «сеть» синхронная; либо beacon недоступен — обычная попытка.
      sendIntegrityEvents(session, batch).catch(() => undefined);
      return;
    }
    const url = `${env.apiUrl}/assessment/sessions/${encodeURIComponent(session)}/events`;
    const blob = new Blob([JSON.stringify({ events: batch })], { type: 'application/json' });
    navigator.sendBeacon(url, blob);
  }, []);

  useEffect(() => {
    if (!enabled || !sessionId) return undefined;
    // Новая сессия — чистое состояние (смена sessionId перезапускает эффект).
    stateRef.current = emptyState();
    const state = stateRef.current;

    const markAway = (): void => {
      if (state.awaySince !== null) return;
      state.awaySince = Date.now();
      push('blur', state.currentItemId ? { item_id: state.currentItemId } : {});
    };
    const markBack = (): void => {
      if (state.awaySince === null) return;
      const durationMs = Date.now() - state.awaySince;
      state.awaySince = null;
      push('focus', { duration_ms: durationMs });
    };

    const onPaste = (event: ClipboardEvent): void => {
      // Берём ТОЛЬКО длину вставки; текст никуда не сохраняется и не отправляется.
      const len = event.clipboardData?.getData('text/plain').length ?? 0;
      if (len === 0) return;
      state.totalPasted += len;
      const itemId = state.currentItemId;
      if (itemId) state.pastedByItem.set(itemId, (state.pastedByItem.get(itemId) ?? 0) + len);
      push('paste', {
        ...(itemId ? { item_id: itemId } : {}),
        len,
        chars_pasted: state.totalPasted,
      });
    };

    const onBlur = (): void => markAway();
    const onFocus = (): void => markBack();
    const onVisibility = (): void => {
      if (document.visibilityState === 'hidden') markAway();
      else markBack();
    };
    const onPageHide = (): void => flushOnPageHide();

    document.addEventListener('paste', onPaste, true);
    window.addEventListener('blur', onBlur);
    window.addEventListener('focus', onFocus);
    document.addEventListener('visibilitychange', onVisibility);
    window.addEventListener('pagehide', onPageHide);
    const timer = window.setInterval(flush, FLUSH_INTERVAL_MS);

    return () => {
      document.removeEventListener('paste', onPaste, true);
      window.removeEventListener('blur', onBlur);
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('visibilitychange', onVisibility);
      window.removeEventListener('pagehide', onPageHide);
      window.clearInterval(timer);
      flush();
    };
  }, [sessionId, enabled, push, flush, flushOnPageHide]);

  const itemShown = useCallback(
    (itemId: string): void => {
      const state = stateRef.current;
      state.currentItemId = itemId;
      state.shownAt.set(itemId, Date.now());
      state.pastedByItem.set(itemId, 0);
      push('item_shown', { item_id: itemId });
    },
    [push],
  );

  const firstInput = useCallback(
    (itemId: string): void => {
      const state = stateRef.current;
      if (state.firstInputSent.has(itemId)) return;
      state.firstInputSent.add(itemId);
      const shown = state.shownAt.get(itemId);
      push('first_input', {
        item_id: itemId,
        ...(shown !== undefined ? { duration_ms: Date.now() - shown } : {}),
      });
    },
    [push],
  );

  const answerSubmitted = useCallback(
    (itemId: string, answerLength: number): void => {
      const state = stateRef.current;
      const shown = state.shownAt.get(itemId);
      const pasted = state.pastedByItem.get(itemId) ?? 0;
      push('answer_submitted', {
        item_id: itemId,
        len: answerLength,
        ...(shown !== undefined ? { duration_ms: Date.now() - shown } : {}),
        chars_typed: Math.max(0, answerLength - pasted),
        chars_pasted: pasted,
      });
      // Ответ — ключевое событие: отправляем очередь сразу, не дожидаясь таймера.
      flush();
    },
    [push, flush],
  );

  return useMemo(
    () => ({ itemShown, firstInput, answerSubmitted, flush }),
    [itemShown, firstInput, answerSubmitted, flush],
  );
}
