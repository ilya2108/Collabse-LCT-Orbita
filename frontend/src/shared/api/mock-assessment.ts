import { ApiError } from './errors';
import { UNHANDLED } from './mock-crm';
import type {
  CandidateCategory,
  TargetGrade,
  TestAnswerBody,
  TestAnswerResult,
  TestFinishResult,
  TestItemBody,
  TestItemCurrent,
  TestMode,
  TestSessionOut,
} from './types';

/**
 * Фикстуры сессий теста на грейд (режим `npm run dev:mock`) — контракт интегратора 11.10:
 * POST /assessment/sessions (201/200/409), GET /assessment/sessions/current,
 * GET …/items/current, POST …/items/{id}/answer, POST …/finish,
 * GET /candidates/me/category. Режимы: express (по умолчанию) — 3 задания по демо-профилю
 * junior be = {B1, B2, D1} (PLAN.md §4.3); full — 6 заданий (добавляются B3, D4, B4).
 * Состояние живёт в памяти вкладки. Телеметрия (`…/events`, `…/integrity`) — в mock-crm.ts.
 */

/** Кулдаун повторного теста в фикстурах — как DEMO_COOLDOWN_SECONDS на стенде. */
const DEMO_COOLDOWN_SECONDS = 60;

interface MockItem {
  body: TestItemBody;
  /** Ключ закрытого задания — только для подсчёта демо-результата, наружу не отдаётся. */
  answerKey?: string;
}

const ITEMS: MockItem[] = [
  {
    answerKey: 'B',
    body: {
      id: 'itm-1',
      node_code: 'be.B1',
      title: 'Проектирование API',
      kind: 'choice',
      time_limit_seconds: 120,
      body_md: [
        'Клиент повторяет `POST /payments` после сетевого таймаута: ответ он не получил,',
        'но платёж на сервере уже создан. Как спроектировать эндпоинт, чтобы повтор',
        'не создавал второй платёж?',
        '',
        'Выберите один вариант и мысленно обоснуйте: что хранит сервер и как долго.',
      ].join('\n'),
      options: [
        { id: 'A', text: 'Вернуть 409 на любой второй POST с тем же телом запроса' },
        {
          id: 'B',
          text: 'Принимать Idempotency-Key: по повтору ключа возвращать сохранённый результат',
        },
        { id: 'C', text: 'Сделать операцию через PUT — он идемпотентен по определению' },
        { id: 'D', text: 'Блокировать таблицу платежей на время обработки запроса' },
      ],
    },
  },
  {
    answerKey: 'C',
    body: {
      id: 'itm-2',
      node_code: 'be.B2',
      title: 'Поддерживаемый код и тесты',
      kind: 'choice',
      time_limit_seconds: 90,
      body_md: [
        'В сервисе 400 e2e-тестов через браузер (40 минут CI, падают «мигая»)',
        'и почти нет модульных. Команда жалуется, что рефакторинг невозможен.',
        'С чего начать выправлять пирамиду тестов?',
      ].join('\n'),
      options: [
        { id: 'A', text: 'Запараллелить e2e на 10 агентов — время CI сократится' },
        { id: 'B', text: 'Удалить мигающие тесты и запретить писать новые e2e' },
        {
          id: 'C',
          text: 'Выделить доменную логику и покрыть её быстрыми модульными тестами, e2e оставить на ключевые сценарии',
        },
        { id: 'D', text: 'Перевести все e2e в скриншотные — они стабильнее' },
      ],
    },
  },
  {
    body: {
      id: 'itm-3',
      node_code: 'be.D1',
      title: 'Язык и рантайм',
      kind: 'text',
      time_limit_seconds: 300,
      options: null,
      body_md: [
        'Обработчик в async-приложении вызывает библиотеку, которая 2–3 секунды',
        'синхронно считает хэш (CPU-bound):',
        '',
        '```python',
        'async def handler(request):',
        '    digest = heavy_hash(request.body)   # синхронно, ~2–3 с CPU',
        '    return {"digest": digest}',
        '```',
        '',
        'Что произойдёт с остальными запросами сервиса и как это исправить?',
        'Коротко: механизм проблемы и 1–2 варианта решения с их ценой (до 800 знаков).',
      ].join('\n'),
    },
  },
];

/** Дополнительные задания полного режима (full → 6 заданий: экспресс + эти три). */
const FULL_EXTRA_ITEMS: MockItem[] = [
  {
    answerKey: 'B',
    body: {
      id: 'itm-4',
      node_code: 'be.B3',
      title: 'Слой данных',
      kind: 'choice',
      time_limit_seconds: 120,
      body_md: [
        'Отчёт по 500 заказам строится 7 секунд: в логе СУБД — сотни одинаковых коротких',
        'запросов `SELECT ... FROM customers WHERE id = $1`. В цикле по заказам читается',
        '`order.customer.name` (ленивое отношение ORM). Как устранить причину?',
      ].join('\n'),
      options: [
        { id: 'A', text: 'Кэшировать клиентов в словаре внутри цикла — повторные чтения бесплатны' },
        {
          id: 'B',
          text: 'Загрузить клиентов вместе с заказами (eager load / JOIN) — один-два запроса вместо N+1',
        },
        { id: 'C', text: 'Поднять пул соединений БД — запросы пойдут параллельно' },
        { id: 'D', text: 'Убрать LIMIT и читать все заказы одним запросом' },
      ],
    },
  },
  {
    answerKey: 'C',
    body: {
      id: 'itm-5',
      node_code: 'be.D4',
      title: 'Устройство СУБД',
      kind: 'choice',
      time_limit_seconds: 120,
      body_md: [
        'Запрос `WHERE status = $1 AND city = $2 ORDER BY created_at DESC LIMIT 20`',
        'делает полное сканирование таблицы в 10 млн строк. Какой индекс закроет его целиком?',
      ].join('\n'),
      options: [
        { id: 'A', text: 'Три отдельных индекса: по status, по city и по created_at' },
        { id: 'B', text: 'Составной индекс (created_at, status, city)' },
        { id: 'C', text: 'Составной индекс (status, city, created_at)' },
        { id: 'D', text: 'Индекс по первичному ключу — он уже есть, нужно только ANALYZE' },
      ],
    },
  },
  {
    body: {
      id: 'itm-6',
      node_code: 'be.B4',
      title: 'Отладка и диагностика',
      kind: 'text',
      time_limit_seconds: 300,
      options: null,
      body_md: [
        'Каждую ночь в 03:10 сервис на 10 минут отвечает таймаутами, днём стабилен,',
        'на стейдже не воспроизводится. Есть логи с request_id, метрики процесса и БД.',
        '',
        'Опишите план диагностики: что сопоставите, какие 2–3 гипотезы проверите первыми',
        'и какие данные подтвердят каждую (до 800 знаков).',
      ].join('\n'),
    },
  },
];

const NODE_TITLES: Record<string, string> = {
  'be.B1': 'Проектирование API',
  'be.B2': 'Поддерживаемый код и тесты',
  'be.D1': 'Язык и рантайм',
  'be.B3': 'Слой данных',
  'be.D4': 'Устройство СУБД',
  'be.B4': 'Отладка и диагностика',
};

interface MockSessionState {
  session: TestSessionOut;
  /** Задания сессии: экспресс — ITEMS (3), полный — ITEMS + FULL_EXTRA_ITEMS (6). */
  items: MockItem[];
  /** Ответы по id задания: choice_id или длина текста (сам текст не нужен). */
  answers: Map<string, { choiceId?: string; textLen?: number }>;
  finished: boolean;
}

const CATEGORY_KEY = 'fsp.mock.category';

/** Категория переживает перезагрузку вкладки (F5 на демо не теряет результат теста). */
function loadCategory(): CandidateCategory | null {
  try {
    const raw = sessionStorage.getItem(CATEGORY_KEY);
    return raw ? (JSON.parse(raw) as CandidateCategory) : null;
  } catch {
    return null;
  }
}

function persistCategory(value: CandidateCategory | null): void {
  try {
    if (value) sessionStorage.setItem(CATEGORY_KEY, JSON.stringify(value));
    else sessionStorage.removeItem(CATEGORY_KEY);
  } catch {
    // sessionStorage недоступен — живём в памяти
  }
}

const COOLDOWN_KEY = 'fsp.mock.nextAttemptAt';

function loadCooldown(): number | null {
  try {
    const raw = sessionStorage.getItem(COOLDOWN_KEY);
    return raw ? Number(raw) : null;
  } catch {
    return null;
  }
}

function persistCooldown(value: number | null): void {
  try {
    if (value) sessionStorage.setItem(COOLDOWN_KEY, String(value));
    else sessionStorage.removeItem(COOLDOWN_KEY);
  } catch {
    // sessionStorage недоступен — живём в памяти
  }
}

let state: MockSessionState | null = null;
let category: CandidateCategory | null = loadCategory();
/** После finish — раньше этой даты новый тест не начать (DEMO_COOLDOWN_SECONDS). */
let nextAttemptAt: number | null = loadCooldown();

/** Сброс (кнопка «Выйти» в фикстурах). */
export function resetMockAssessment(): void {
  state = null;
  category = null;
  nextAttemptAt = null;
  persistCategory(null);
  persistCooldown(null);
}

function conflict(code: string, message: string, details: ApiError['details'] = []): ApiError {
  return new ApiError(409, code, message, details, 'mock');
}

function notFound(code: string, message: string): ApiError {
  return new ApiError(404, code, message, [], 'mock');
}

function currentIndex(s: MockSessionState): number {
  return s.items.findIndex((item) => !s.answers.has(item.body.id));
}

function sessionOut(s: MockSessionState): TestSessionOut {
  const index = currentIndex(s);
  return { ...s.session, current_index: index === -1 ? s.items.length : index };
}

function createSession(
  targetGrade: TargetGrade,
  specialization: string | null,
  mode: TestMode | undefined,
): TestSessionOut {
  if (state && !state.finished) return sessionOut(state); // активная возвращается как есть
  if (!specialization) {
    throw conflict(
      'profile_incomplete',
      'В профиле не выбрана специализация — выберите её на странице «Профиль»',
    );
  }
  if (nextAttemptAt !== null && Date.now() < nextAttemptAt) {
    const iso = new Date(nextAttemptAt).toISOString();
    throw conflict('cooldown_active', 'Повторный тест пока недоступен', [
      { field: 'next_attempt_at', code: 'cooldown_active', message: iso },
    ]);
  }
  const effectiveMode: TestMode = mode ?? 'express'; // без mode стенд в демо-режиме
  const items = effectiveMode === 'full' ? [...ITEMS, ...FULL_EXTRA_ITEMS] : ITEMS;
  state = {
    session: {
      id: 'ses-demo-1',
      status: 'active',
      specialization,
      target_grade: targetGrade,
      mode: effectiveMode,
      items_total: items.length,
      current_index: 0,
    },
    items,
    answers: new Map(),
    finished: false,
  };
  return sessionOut(state);
}

function getCurrentItem(sessionId: string): TestItemCurrent {
  if (!state || state.finished || state.session.id !== sessionId) {
    throw notFound('not_found', 'Активная сессия теста не найдена');
  }
  const index = currentIndex(state);
  if (index === -1) throw notFound('not_found', 'Все задания отвечены — завершите сессию');
  const item = state.items[index];
  if (!item) throw notFound('not_found', 'Задание не найдено');
  return { index, total: state.items.length, item: item.body };
}

function submitAnswer(sessionId: string, itemId: string, body: TestAnswerBody): TestAnswerResult {
  if (!state || state.finished || state.session.id !== sessionId) {
    throw notFound('not_found', 'Активная сессия теста не найдена');
  }
  const item = state.items.find((i) => i.body.id === itemId);
  if (!item) throw notFound('not_found', 'Задание не найдено');
  if (state.answers.has(itemId)) {
    throw conflict('item_already_answered', 'Ответ на это задание уже принят');
  }
  if (item.body.kind === 'choice' && !body.choice_id) {
    throw new ApiError(400, 'validation_error', 'Для закрытого задания нужен choice_id', [], 'mock');
  }
  if (item.body.kind === 'text' && !body.text) {
    throw new ApiError(400, 'validation_error', 'Для открытого задания нужен text', [], 'mock');
  }
  state.answers.set(itemId, { choiceId: body.choice_id, textLen: body.text?.length });
  const next = currentIndex(state);
  return { accepted: true, finished: next === -1, next_index: next === -1 ? null : next };
}

function finishSession(sessionId: string, specialization: string | null): TestFinishResult {
  if (!state || state.session.id !== sessionId) {
    throw notFound('not_found', 'Сессия теста не найдена');
  }
  const s = state;
  const nodes = s.items.map((item) => {
    const answer = s.answers.get(item.body.id);
    let score = 0;
    if (item.body.kind === 'choice') {
      score = answer?.choiceId === item.answerKey ? 0.9 : 0.3;
    } else {
      // Открытый ответ в фикстурах «судится» длиной: развёрнутый ответ набирает больше
      score = Math.min(0.55 + (answer?.textLen ?? 0) / 800, 0.92);
    }
    return {
      code: item.body.node_code,
      title: NODE_TITLES[item.body.node_code] ?? item.body.node_code,
      score: Math.round(score * 100) / 100,
      state: (score >= 0.85 ? 'fresh' : score >= 0.5 ? 'fading' : 'off') as TestFinishNodeState,
    };
  });
  const active = nodes.filter((n) => n.state !== 'off').length;
  const passed = active >= 2; // демо-профиль junior: ≥ 2 из 3 активны, Q ≥ 1
  const qLevel = Math.max(active - 1, 0);
  s.finished = true;
  nextAttemptAt = Date.now() + DEMO_COOLDOWN_SECONDS * 1000;
  persistCooldown(nextAttemptAt);
  if (passed) {
    category = {
      specialization: specialization ?? s.session.specialization,
      grade: s.session.target_grade,
      grade_confirmed: true,
      q_level: qLevel,
      computed_at: new Date().toISOString(),
    };
    persistCategory(category);
  }
  return {
    status: 'graded',
    grade: passed ? s.session.target_grade : (category?.grade ?? null),
    grade_confirmed: passed,
    q_level: qLevel,
    nodes,
    integrity: { level: 'high', flags: [] },
  };
}

type TestFinishNodeState = TestFinishResult['nodes'][number]['state'];

/**
 * Диспетчер фикстур сессий теста; UNHANDLED — путь не из этого модуля.
 * `specialization` — из мок-профиля кандидата (нужна для profile_incomplete и категории).
 */
export function mockAssessmentRequest<T>(
  method: string,
  path: string,
  body: unknown,
  specialization: string | null,
): T | typeof UNHANDLED {
  const key = `${method.toUpperCase()} ${path}`;

  if (key === 'POST /assessment/sessions') {
    const payload = (body ?? {}) as { target_grade?: TargetGrade; mode?: TestMode };
    return createSession(payload.target_grade ?? 'junior', specialization, payload.mode) as T;
  }
  if (key === 'GET /assessment/sessions/current') {
    if (!state || state.finished) throw notFound('not_found', 'Активной сессии теста нет');
    return sessionOut(state) as T;
  }
  const itemMatch = /^GET \/assessment\/sessions\/([^/]+)\/items\/current$/.exec(key);
  if (itemMatch) return getCurrentItem(decodeURIComponent(itemMatch[1] ?? '')) as T;

  const answerMatch = /^POST \/assessment\/sessions\/([^/]+)\/items\/([^/]+)\/answer$/.exec(key);
  if (answerMatch) {
    return submitAnswer(
      decodeURIComponent(answerMatch[1] ?? ''),
      decodeURIComponent(answerMatch[2] ?? ''),
      (body ?? {}) as TestAnswerBody,
    ) as T;
  }
  const finishMatch = /^POST \/assessment\/sessions\/([^/]+)\/finish$/.exec(key);
  if (finishMatch) return finishSession(decodeURIComponent(finishMatch[1] ?? ''), specialization) as T;

  if (key === 'GET /candidates/me/category') {
    if (!category) {
      throw notFound(
        'category_not_assigned',
        'Категория ещё не присвоена: заполните профиль и пройдите тест на грейд',
      );
    }
    return category as T;
  }

  return UNHANDLED;
}
