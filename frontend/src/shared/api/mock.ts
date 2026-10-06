import { ApiError } from './errors';
import { mockAssessmentRequest, resetMockAssessment } from './mock-assessment';
import { mockCrmRequest, UNHANDLED } from './mock-crm';
import type {
  CandidateProfile,
  ConsentBody,
  CurrentUser,
  Industry,
  SetRoleBody,
  SkillEdge,
  SkillGraph,
  SkillNode,
  Specialization,
} from './types';

/** Пустой профиль кандидата — как backend создаёт при первом `GET /candidates/me`. */
function emptyProfile(): CandidateProfile {
  const now = new Date().toISOString();
  return {
    id: 'c-1',
    pseudonym: 'B-7F3A',
    full_name: null,
    phone: null,
    telegram: null,
    city: null,
    industry_code: null,
    specialization: null,
    stack_tags_declared: [],
    stack_tags_confirmed: [],
    experience_years: null,
    roles_declared: [],
    soft_skills_declared: [],
    about: null,
    salary_floor: null,
    privacy: {
      searchable: true,
      show_city: true,
      show_fsp: true,
      show_accomplishments: true,
      hidden_company_ids: [],
    },
    searchable: false,
    fsp_id: null,
    fsp_linked_at: null,
    created_at: now,
    updated_at: now,
  };
}

/**
 * Режим фикстур (`MOCK_API=1` в /config.js или `VITE_MOCK_API=1`): UI работает без backend
 * и Keycloak — для вёрстки, скриншотов и демонстрации каркаса. Формы ответов повторяют
 * контракт CLAUDE.md «Контракты API»; графы — be/fe по PLAN.md §3.2 (упрощённые описания).
 * Состояние «пользователя» живёт в sessionStorage, чтобы пройти онбординг и перезагрузку.
 * В продакшен-сборке режим включается только явной переменной — по умолчанию выключен.
 */

const STORAGE_KEY = 'fsp.mock.user';
const AUTH_KEY = 'fsp.mock.authenticated';
const PROFILE_KEY = 'fsp.mock.profile';

/** Профиль переживает перезагрузку вкладки (F5 на демо не ломает путь кандидата). */
function loadProfile(): CandidateProfile | null {
  try {
    const raw = sessionStorage.getItem(PROFILE_KEY);
    return raw ? (JSON.parse(raw) as CandidateProfile) : null;
  } catch {
    return null;
  }
}

function persistProfile(value: CandidateProfile | null): void {
  try {
    if (value) sessionStorage.setItem(PROFILE_KEY, JSON.stringify(value));
    else sessionStorage.removeItem(PROFILE_KEY);
  } catch {
    // sessionStorage недоступен — живём в памяти
  }
}

let profile: CandidateProfile | null = loadProfile();

function nowIso(): string {
  return new Date().toISOString();
}

function defaultUser(): CurrentUser {
  return {
    id: '00000000-0000-4000-8000-000000000001',
    email: 'demo@fsp.local',
    email_verified: true,
    roles: [],
    active_role: null,
    consent: { pdn_at: null, policy_version: null, publish_at: null },
    created_at: nowIso(),
  };
}

function loadUser(): CurrentUser {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw) as CurrentUser;
  } catch {
    // sessionStorage недоступен (приватный режим) — работаем в памяти
  }
  return defaultUser();
}

function saveUser(user: CurrentUser): CurrentUser {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(user));
  } catch {
    // см. loadUser
  }
  return user;
}

/** «Аутентифицирован» ли мок-пользователь: до нажатия «Войти» показываем /login как в бою. */
export function mockIsAuthenticated(): boolean {
  try {
    return sessionStorage.getItem(AUTH_KEY) === '1';
  } catch {
    return true;
  }
}

/** Имитация редиректа на Keycloak и обратно: отмечаем вход и переходим на redirectPath. */
export function mockLogin(redirectPath = '/'): void {
  try {
    sessionStorage.setItem(AUTH_KEY, '1');
  } catch {
    // см. loadUser
  }
  window.location.assign(redirectPath);
}

/** Сбросить мок-пользователя и «выйти» (кнопка «Выйти» в режиме фикстур). */
export function resetMockUser(): void {
  try {
    sessionStorage.removeItem(STORAGE_KEY);
    sessionStorage.removeItem(AUTH_KEY);
  } catch {
    // см. loadUser
  }
  profile = null;
  persistProfile(null);
  resetMockAssessment();
}

// ---------------------------------------------------------------------------
// Графы специализаций (PLAN.md §3.2): 13 узлов, рёбра D→B, B→C, боковые, мосты
// ---------------------------------------------------------------------------

type NodeSpec = [code: string, title: string, description: string];

function buildNodes(spec: string, items: NodeSpec[]): SkillNode[] {
  return items.map(([code, title, description]) => ({
    id: `${spec}.${code}`,
    code,
    layer: code[0] as SkillNode['layer'],
    title,
    description,
  }));
}

function buildEdges(spec: string, pairs: string, kind: SkillEdge['kind']): SkillEdge[] {
  return pairs
    .split(/[,\s]+/)
    .filter(Boolean)
    .map((pair) => {
      const [a, b] = pair.split('-');
      return { source: `${spec}.${a}`, target: `${spec}.${b}`, kind };
    });
}

const BE_NODES = buildNodes('be', [
  ['D1', 'Язык и рантайм', 'Типизация, модель памяти, async/await, стандартная библиотека'],
  ['D2', 'Структуры данных и алгоритмы под задачу', 'Выбор структуры, сложность, компромиссы память/время'],
  ['D3', 'Конкурентность и ресурсы', 'Потоки, event loop, пулы, утечки, backpressure'],
  ['D4', 'Устройство СУБД', 'Индексы, планы, транзакции, изоляция, блокировки'],
  ['B1', 'Проектирование API', 'HTTP/REST, контракты, версии, ошибки, идемпотентность'],
  ['B2', 'Поддерживаемый код и тесты', 'Модульность, рефакторинг, пирамида тестов'],
  ['B3', 'Слой данных', 'Моделирование, ORM, миграции, целостность'],
  ['B4', 'Отладка и диагностика', 'Воспроизведение, профилирование, чтение логов и трасс'],
  ['B5', 'Безопасность приложения', 'Authn/authz, OWASP Top 10, секреты, ПДн'],
  ['C1', 'Поставка: CI/CD и контейнеры', 'Docker, пайплайны, конфигурация, релизы'],
  ['C2', 'Распределённые системы и интеграции', 'Очереди, кэш, идемпотентность, саги, ретраи'],
  ['C3', 'Наблюдаемость и эксплуатация', 'Логи, метрики, трассы, инциденты, SLO'],
  ['C4', 'Командная разработка и декомпозиция', 'Ревью, git-процесс, оценка, техрешения'],
]);

const BE_EDGES: SkillEdge[] = [
  ...buildEdges('be', 'D1-B1 D1-B2 D2-B3 D2-B4 D3-B1 D3-B4 D4-B3 D4-B4', 'vertical'),
  ...buildEdges('be', 'B1-C2 B1-C4 B2-C1 B2-C4 B3-C2 B4-C3 B5-C1 B5-C3', 'vertical'),
  ...buildEdges('be', 'D1-D3 D2-D3 D3-D4', 'lateral'),
  ...buildEdges('be', 'B1-B2 B1-B3 B2-B3 B2-B4 B3-B4 B4-B5 B1-B5', 'lateral'),
  ...buildEdges('be', 'C1-C3 C2-C3 C1-C4 C2-C4', 'lateral'),
];

const FE_NODES = buildNodes('fe', [
  ['D1', 'JS/TS и рантайм браузера', 'Event loop, типы, модули'],
  ['D2', 'Структуры данных и алгоритмы для UI', 'Списки, деревья, индексы, сложность на клиенте'],
  ['D3', 'Рендеринг и производительность страницы', 'DOM, CSS-движок, reflow, метрики'],
  ['D4', 'Сеть и протоколы', 'HTTP, кэш, CORS'],
  ['B1', 'Компонентная архитектура (React)', 'Композиция, хуки, границы ответственности'],
  ['B2', 'Состояние и данные', 'Серверное и клиентское состояние, кэш запросов'],
  ['B3', 'Вёрстка, адаптивность, доступность', 'Семантика, адаптив, a11y'],
  ['B4', 'Тестирование и отладка фронтенда', 'Юнит/интеграция/e2e, DevTools'],
  ['B5', 'Безопасность клиента', 'XSS, CSRF, auth-потоки'],
  ['C1', 'Сборка и поставка', 'Vite, CI, мониторинг ошибок'],
  ['C2', 'Интеграция с API и контрактами', 'Типизация контрактов, ошибки, версии'],
  ['C3', 'Дизайн-системы и UX-инженерия', 'Токены, компоненты, UX-паттерны'],
  ['C4', 'Командная разработка и декомпозиция', 'Ревью, git-процесс, оценка'],
]);

const FE_EDGES: SkillEdge[] = [
  ...buildEdges('fe', 'D1-B1 D1-B2 D2-B2 D3-B1 D3-B3 D4-B2 D4-B5', 'vertical'),
  ...buildEdges('fe', 'B1-C3 B1-C4 B2-C2 B3-C3 B4-C1 B5-C2 B5-C1', 'vertical'),
  ...buildEdges('fe', 'D1-D3 D2-D3 D3-D4', 'lateral'),
  ...buildEdges('fe', 'B1-B2 B1-B3 B2-B4 B3-B4 B4-B5 B2-B5', 'lateral'),
  ...buildEdges('fe', 'C1-C2 C2-C3 C1-C4 C3-C4', 'lateral'),
];

const BRIDGES: SkillEdge[] = [
  { source: 'be.B1', target: 'fe.C2', kind: 'bridge' },
  { source: 'be.C1', target: 'fe.C1', kind: 'bridge' },
  { source: 'be.B5', target: 'fe.B5', kind: 'bridge' },
  { source: 'be.C4', target: 'fe.C4', kind: 'bridge' },
];

const LAYERS: SkillGraph['layers'] = [
  { code: 'D', title: 'Глубина' },
  { code: 'B', title: 'База' },
  { code: 'C', title: 'Контекст' },
];

export const GRAPHS: Record<string, SkillGraph> = {
  be: {
    specialization: { code: 'be', title: 'Бэкенд', status: 'active', graph_version: '2026-10-06' },
    layers: LAYERS,
    nodes: BE_NODES,
    edges: BE_EDGES,
    bridges: BRIDGES.filter((b) => b.source.startsWith('be.')),
  },
  fe: {
    specialization: { code: 'fe', title: 'Фронтенд', status: 'active', graph_version: '2026-10-06' },
    layers: LAYERS,
    nodes: FE_NODES,
    edges: FE_EDGES,
    bridges: BRIDGES.map((b) => ({ source: b.target, target: b.source, kind: 'bridge' as const })),
  },
};

const SPECIALIZATIONS: Specialization[] = [
  { code: 'be', title: 'Бэкенд', status: 'active', graph_version: '2026-10-06', node_count: 13 },
  { code: 'fe', title: 'Фронтенд', status: 'active', graph_version: '2026-10-06', node_count: 13 },
  { code: 'data', title: 'Данные', status: 'preview', graph_version: '2026-10-06', node_count: 0 },
];

const INDUSTRIES: Industry[] = [
  { code: 'it', title: 'ИТ-продукты' },
  { code: 'fintech', title: 'Финтех' },
  { code: 'ecom', title: 'E-commerce' },
  { code: 'gov', title: 'Госсектор' },
  { code: 'telecom', title: 'Телеком' },
  { code: 'industry', title: 'Промышленность' },
  { code: 'edu', title: 'Образование' },
  { code: 'other', title: 'Другое' },
];


function delay(ms = 250): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function notFound(message: string): ApiError {
  return new ApiError(404, 'not_found', message, [], 'mock');
}

/** Диспетчер фикстур: метод + путь → ответ или ApiError в формате backend. */
export async function mockRequest<T>(method: string, path: string, body?: unknown): Promise<T> {
  await delay();
  const key = `${method.toUpperCase()} ${path}`;

  if (key === 'GET /auth/me') return loadUser() as T;

  if (key === 'POST /auth/role') {
    const user = loadUser();
    const { role } = body as SetRoleBody;
    if (user.roles.some((r) => r === 'candidate' || r === 'employer')) {
      throw new ApiError(409, 'role_already_set', 'Роль уже выбрана', [], 'mock');
    }
    return saveUser({ ...user, roles: [...user.roles, role], active_role: role }) as T;
  }

  if (key === 'POST /auth/consent') {
    const user = loadUser();
    const { policy_version, publish } = body as ConsentBody;
    return saveUser({
      ...user,
      consent: {
        pdn_at: nowIso(),
        policy_version,
        publish_at: publish ? nowIso() : null,
      },
    }) as T;
  }

  if (key === 'DELETE /auth/account') {
    throw new ApiError(501, 'not_implemented', 'Удаление аккаунта появится позже', [], 'mock');
  }

  if (key === 'GET /skills/industries') return INDUSTRIES as T;
  if (key === 'GET /skills/specializations') return SPECIALIZATIONS as T;

  const graphMatch = /^GET \/skills\/graph\/([^/]+)$/.exec(key);
  if (graphMatch) {
    const graph = GRAPHS[decodeURIComponent(graphMatch[1] ?? '')];
    if (!graph) throw notFound('Специализация не найдена или граф ещё не опубликован');
    return graph as T;
  }

  if (key === 'GET /candidates/me') {
    if (!profile) throw notFound('Профиль кандидата ещё не создан');
    return profile as T;
  }

  // Сессии теста на грейд и категория — отдельный модуль фикстур
  if (/^POST \/assessment\/sessions\/[^/]+\/finish$/.test(key)) {
    await delay(1800); // грейдинг «думает» — виден экран «Проверяем ответы…»
  }
  const assessment = mockAssessmentRequest<T>(
    method,
    path,
    body,
    profile?.specialization ?? null,
  );
  if (assessment !== UNHANDLED) return assessment;

  // CRM работодателя, приглашения, телеграм-бот — отдельный модуль фикстур
  const crm = mockCrmRequest<T>(method, path, body);
  if (crm !== UNHANDLED) return crm;
  if (key === 'PUT /candidates/me') {
    profile = {
      ...emptyProfile(),
      ...(profile ?? {}),
      ...(body as Partial<CandidateProfile>),
    };
    persistProfile(profile);
    return profile as T;
  }

  throw notFound(`Фикстуры для ${key} нет`);
}
