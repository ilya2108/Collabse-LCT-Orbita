import { PIPELINE_STAGE_ORDER } from '@/shared/config/crm';
import { ApiError } from './errors';
import { GRAPHS } from './mock';
import type {
  CandidateContacts,
  CompanyContacts,
  CompanyOut,
  CompanyUpdate,
  IntegrityEventsBody,
  IntegrityLevel,
  IntegrityReport,
  InvitationAcceptResult,
  InvitationCreateBody,
  InvitationCreateResult,
  InvitationDeclineBody,
  InvitationOut,
  InvitationStatus,
  MemberInviteBody,
  MemberInviteResult,
  MemberOut,
  NeedCandidatesOut,
  NeedCreateBody,
  NeedGrade,
  NeedOut,
  NeedPipelineOut,
  PipelineItem,
  PipelineStage,
  PublicInviteInfo,
  RankedCandidate,
  TelegramLinkStatus,
  TelegramLinkToken,
  TmaMe,
} from './types';

/**
 * Фикстуры мини-CRM работодателя, приглашений и телеграм-бота (режим `npm run dev:mock`).
 * Формы ответов повторяют контракты агента ядра (employers/needs/pipeline/invitations);
 * данные детерминированы (seeded PRNG) и живут в памяти вкладки: демо-масштаб —
 * 28 кандидатов, 2 потребности, воронка middle-потребности со всеми стадиями.
 * Телеграм-эндпоинты (`/auth/telegram/*`) — зеркалят контракт модуля tgbot backend.
 */

/** Сентинел «путь не наш» — mock.ts продолжает собственную диспетчеризацию. */
export const UNHANDLED: unique symbol = Symbol('mock-crm-unhandled');

// ---------------------------------------------------------------------------
// Детерминированный генератор (Random(2026), как в сидax backend)
// ---------------------------------------------------------------------------

function mulberry32(seed: number): () => number {
  let a = seed;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const rng = mulberry32(2026);

function pick<T>(items: readonly T[]): T {
  return items[Math.floor(rng() * items.length)] as T;
}

function hex(len: number): string {
  let out = '';
  for (let i = 0; i < len; i += 1) out += '0123456789abcdef'[Math.floor(rng() * 16)];
  return out;
}

function nowIso(): string {
  return new Date().toISOString();
}

function daysFromNow(days: number): string {
  return new Date(Date.now() + days * 24 * 3600 * 1000).toISOString();
}

function conflict(code: string, message: string): ApiError {
  return new ApiError(409, code, message, [], 'mock');
}

function notFound(message: string): ApiError {
  return new ApiError(404, 'not_found', message, [], 'mock');
}

function forbidden(code: string, message: string): ApiError {
  return new ApiError(403, code, message, [], 'mock');
}

function badRequest(message: string): ApiError {
  return new ApiError(400, 'validation_error', message, [], 'mock');
}

// ---------------------------------------------------------------------------
// Кандидаты банка: 12 junior / 10 middle / 6 senior (как в сидax backend)
// ---------------------------------------------------------------------------

interface MockCandidate {
  id: string;
  pseudonym: string;
  full_name: string;
  grade: NeedGrade;
  grade_confirmed: boolean;
  q_level: number;
  shape: string;
  salary_floor: number | null;
  integrity_level: IntegrityLevel | null;
}

/** Цикл уровней целостности: большинство сессий чистые. Позиции 3 и 7 не используются
 * (каждый четвёртый кандидат без подтверждённого грейда получает null напрямую). */
const INTEGRITY_CYCLE: readonly (IntegrityLevel | null)[] = [
  'high', 'medium', 'high', null, 'low', 'high', 'high', null,
];

const SURNAMES = [
  'Смирнова', 'Иванов', 'Кузнецов', 'Соколова', 'Попов', 'Лебедева', 'Козлов', 'Новикова',
  'Морозов', 'Петрова', 'Волков', 'Соловьёва', 'Васильев', 'Зайцева', 'Павлов', 'Семёнова',
  'Голубев', 'Виноградова', 'Богданов', 'Воробьёва', 'Фёдоров', 'Михайлова', 'Беляев',
  'Тарасова', 'Белов', 'Комарова', 'Орлов', 'Киселёва',
] as const;

const INITIALS = ['А.', 'Д.', 'Е.', 'И.', 'К.', 'М.', 'Н.', 'О.', 'П.', 'С.'] as const;

function buildCandidates(): MockCandidate[] {
  const grades: NeedGrade[] = [
    ...Array.from({ length: 12 }, () => 'junior' as const),
    ...Array.from({ length: 10 }, () => 'middle' as const),
    ...Array.from({ length: 6 }, () => 'senior' as const),
  ];
  return grades.map((grade, i) => ({
    id: `cand-${String(i).padStart(2, '0')}`,
    pseudonym: `B-${hex(4).toUpperCase()}`,
    full_name: `${SURNAMES[i % SURNAMES.length]} ${pick(INITIALS)}`,
    grade,
    // каждый четвёртый — грейд заявлен, но не подтверждён тестом
    grade_confirmed: i % 4 !== 3,
    q_level: 1 + Math.floor(rng() * 3),
    shape: pick(['T', 'I', 'П']),
    // каждый третий middle — зарплатная планка 200к (демо «ниже планки»)
    salary_floor: grade === 'middle' && i % 3 === 0 ? 200_000 : null,
    // неподтверждённый грейд = завершённой сессии нет → и сигналов целостности нет
    integrity_level: i % 4 !== 3 ? (INTEGRITY_CYCLE[i % INTEGRITY_CYCLE.length] ?? null) : null,
  }));
}

const CANDIDATES = buildCandidates();

function candidateById(id: string): MockCandidate {
  const found = CANDIDATES.find((c) => c.id === id);
  if (!found) throw notFound('Кандидат не найден');
  return found;
}

// ---------------------------------------------------------------------------
// Компания и участники
// ---------------------------------------------------------------------------

const company: CompanyOut = {
  id: 'comp-1',
  kind: 'corporate',
  name: 'ФСП.Орбита Демо',
  inn: '7700000000',
  description: 'Демонстрационная компания стенда: платёжные сервисы и внутренние платформы.',
  industry_code: 'fintech',
  website: 'https://orbita.fsp-demo.ru',
  contact_email: 'hr@orbita-demo.ru',
  contact_phone: '+7 495 000-00-00',
  my_role: 'owner',
  members_count: 3,
  created_at: '2026-10-01T09:00:00Z',
  updated_at: '2026-10-06T12:00:00Z',
};

let memberSeq = 3;
const members: MemberOut[] = [
  {
    id: 'mem-1',
    role: 'owner',
    status: 'active',
    full_name_masked: 'С*** Е.',
    email_masked: 'j***r@fsp.local',
    telegram_masked: null,
    invite_channel: null,
    has_account: true,
    invited_at: null,
    accepted_at: '2026-10-01T09:00:00Z',
  },
  {
    id: 'mem-2',
    role: 'hr',
    status: 'invited',
    full_name_masked: 'О*** А.',
    email_masked: null,
    telegram_masked: '@o***a',
    invite_channel: 'telegram',
    has_account: false,
    invited_at: '2026-10-05T10:00:00Z',
    accepted_at: null,
  },
  {
    id: 'mem-3',
    role: 'hiring_manager',
    status: 'active',
    full_name_masked: 'К*** Д.',
    email_masked: 'd***v@orbita-demo.ru',
    telegram_masked: null,
    invite_channel: 'email',
    has_account: true,
    invited_at: '2026-10-02T11:00:00Z',
    accepted_at: '2026-10-03T08:30:00Z',
  },
];

/** Токен → id участника; фиксированный демо-токен — как в сиде backend. */
const inviteTokens = new Map<string, string>([['demo-orbita-hr-invite-2026', 'mem-2']]);

function maskContact(value: string): string {
  if (value.includes('@') && !value.startsWith('@')) {
    const [local = '', domain = ''] = value.split('@');
    return `${local[0] ?? ''}***@${domain}`;
  }
  return `${value.slice(0, 2)}***${value.slice(-1)}`;
}

// ---------------------------------------------------------------------------
// Потребности
// ---------------------------------------------------------------------------

let needSeq = 2;
const needs: NeedOut[] = [
  {
    id: 'need-1',
    title: 'Backend-разработчик в платёжный сервис',
    raw_text:
      'Ищем middle-инженера в команду платёжного шлюза: REST API, интеграции с банками, высокие требования к надёжности и идемпотентности.',
    first_months_tasks:
      'Перевести выплаты на новый контракт провайдера, закрыть техдолг по ретраям, настроить алерты.',
    specialization: 'be',
    grade: 'middle',
    stack_tags: ['python', 'postgresql', 'fastapi', 'rabbitmq'],
    industry_code: 'fintech',
    salary_from: 180_000,
    salary_to: 260_000,
    format: 'hybrid',
    status: 'active',
    created_at: '2026-10-04T10:00:00Z',
    updated_at: '2026-10-06T10:00:00Z',
  },
  {
    id: 'need-2',
    title: 'Начинающий backend-инженер во внутренние сервисы',
    raw_text:
      'Junior в команду внутренних инструментов: CRUD-сервисы, фоновые задачи, готовы растить.',
    first_months_tasks: 'Сервис уведомлений под наставничеством, тесты, мелкие баги.',
    specialization: 'be',
    grade: 'junior',
    stack_tags: ['python', 'postgresql'],
    industry_code: 'it',
    salary_from: 80_000,
    salary_to: 130_000,
    format: 'office',
    status: 'active',
    created_at: '2026-10-05T10:00:00Z',
    updated_at: '2026-10-05T10:00:00Z',
  },
];

function needById(id: string): NeedOut {
  const found = needs.find((n) => n.id === id);
  if (!found) throw notFound('Потребность не найдена');
  return found;
}

// ---------------------------------------------------------------------------
// Ранжирование (заглушка §5.3, как на backend: покрытие узлов подграфа)
// ---------------------------------------------------------------------------

const TARGET_NODES: Record<NeedGrade, string[]> = {
  junior: ['be.D1', 'be.D2', 'be.D4', 'be.B1', 'be.B2', 'be.B3'],
  middle: ['be.B1', 'be.B2', 'be.B3', 'be.B4', 'be.B5', 'be.C1', 'be.C2', 'be.C3'],
  senior: ['be.B1', 'be.B4', 'be.B5', 'be.C1', 'be.C2', 'be.C3', 'be.C4'],
};

function nodeTitle(nodeId: string): string {
  const [spec = '', code = ''] = nodeId.split('.');
  const node = GRAPHS[spec]?.nodes.find((n) => n.code === code);
  return node ? node.title : nodeId;
}

const rankingCache = new Map<string, RankedCandidate[]>();

function rankCandidates(need: NeedOut): RankedCandidate[] {
  const cached = rankingCache.get(need.id);
  if (cached) return cached;
  const targets = TARGET_NODES[need.grade];
  const seeded = mulberry32(need.id.length * 1000 + need.grade.length);
  const ranked = CANDIDATES.map((c) => {
    const gradeMatch = c.grade === need.grade;
    let score = gradeMatch ? 0.58 + seeded() * 0.4 : 0.12 + seeded() * 0.34;
    if (!c.grade_confirmed) score *= 0.82;
    score = Math.min(0.98, Math.round(score * 100) / 100);
    const matchedCount = Math.max(1, Math.round(score * targets.length));
    const partialCount = Math.min(targets.length - matchedCount, matchedCount > 1 ? 1 : 2);
    const matched = targets.slice(0, matchedCount);
    const partial = targets.slice(matchedCount, matchedCount + partialCount);
    const missing = targets.slice(matchedCount + partialCount);
    const strongest = matched.slice(0, 2).map(nodeTitle).join(', ');
    return {
      candidate_id: c.id,
      pseudonym: c.pseudonym,
      score,
      coverage: Math.round((matchedCount / targets.length) * 100) / 100,
      grade: c.grade,
      grade_confirmed: c.grade_confirmed,
      q_level: c.q_level,
      shape: c.shape,
      integrity_level: c.integrity_level,
      explanation: {
        basis: 'subgraph' as const,
        matched_nodes: matched,
        partial_nodes: partial,
        missing_nodes: missing,
        text: `Покрывает ${matchedCount} из ${targets.length} узлов подграфа потребности${
          strongest ? `; сильные стороны — ${strongest.toLowerCase()}` : ''
        }${c.grade_confirmed ? '' : '. Грейд заявлен анкетой и ещё не подтверждён тестом'}.`,
      },
    } satisfies RankedCandidate;
  }).sort((a, b) => {
    const aMatch = a.grade === need.grade ? 1 : 0;
    const bMatch = b.grade === need.grade ? 1 : 0;
    // несовпадающий грейд всегда ниже
    if (aMatch !== bMatch) return bMatch - aMatch;
    return b.score - a.score;
  });
  rankingCache.set(need.id, ranked);
  return ranked;
}

function rankedScore(needId: string, candidateId: string): number | null {
  const ranked = rankingCache.get(needId);
  return ranked?.find((r) => r.candidate_id === candidateId)?.score ?? null;
}

// ---------------------------------------------------------------------------
// Приглашения (стороны employer и candidate) и пайплайн
// ---------------------------------------------------------------------------

interface MockInvitation extends Omit<InvitationOut, 'candidate_pseudonym' | 'company_name'> {
  side: 'employer' | 'candidate';
  candidate_id: string | null;
  company_name: string;
  company_contacts: CompanyContacts;
}

interface PipelineEntry {
  candidate_id: string;
  stage: PipelineStage;
  note: string | null;
  invitation_id: string | null;
  updated_at: string;
}

let invitationSeq = 0;

function makeInvitation(partial: Partial<MockInvitation> & { side: MockInvitation['side'] }): MockInvitation {
  invitationSeq += 1;
  return {
    id: `inv-${invitationSeq}`,
    need_id: null,
    candidate_id: null,
    title: null,
    message: null,
    salary_from: 0,
    salary_to: 0,
    contact_method: 'telegram',
    explanation: {},
    status: 'sent',
    sent_at: nowIso(),
    viewed_at: null,
    decided_at: null,
    expires_at: daysFromNow(14),
    decline_reason_code: null,
    decline_text: null,
    company_name: company.name,
    company_contacts: {
      company_name: company.name,
      contact_method: 'telegram',
      contact_email: company.contact_email,
      contact_phone: company.contact_phone,
    },
    ...partial,
  };
}

const invitations: MockInvitation[] = [];

// Воронка middle-потребности: shortlist → invited (sent) → accepted → interview
const pipelines = new Map<string, PipelineEntry[]>();

function seedFunnel(): void {
  const need = needById('need-1');
  const ranked = rankCandidates(need);
  const [first, second, third, fourth, fifth] = ranked;
  if (!first || !second || !third || !fourth || !fifth) return;

  const invited = makeInvitation({
    side: 'employer',
    need_id: need.id,
    candidate_id: second.candidate_id,
    title: need.title,
    message: 'Ваш граф закрывает ядро нашего подграфа — приходите поговорить о платёжном шлюзе.',
    salary_from: need.salary_from,
    salary_to: need.salary_to,
    explanation: { basis: 'subgraph', text: second.explanation.text },
    sent_at: '2026-10-06T09:00:00Z',
    expires_at: daysFromNow(12),
  });
  const accepted = makeInvitation({
    side: 'employer',
    need_id: need.id,
    candidate_id: third.candidate_id,
    title: need.title,
    message: 'Сильное покрытие слоя B — обсудим задачи первых трёх месяцев?',
    salary_from: need.salary_from,
    salary_to: need.salary_to,
    explanation: { basis: 'subgraph', text: third.explanation.text },
    status: 'accepted',
    sent_at: '2026-10-05T09:00:00Z',
    viewed_at: '2026-10-05T12:00:00Z',
    decided_at: '2026-10-05T18:40:00Z',
    expires_at: daysFromNow(11),
  });
  const interviewed = makeInvitation({
    side: 'employer',
    need_id: need.id,
    candidate_id: fourth.candidate_id,
    title: need.title,
    salary_from: need.salary_from,
    salary_to: need.salary_to,
    explanation: { basis: 'subgraph', text: fourth.explanation.text },
    status: 'accepted',
    sent_at: '2026-10-04T09:00:00Z',
    viewed_at: '2026-10-04T10:00:00Z',
    decided_at: '2026-10-04T15:00:00Z',
    expires_at: daysFromNow(10),
  });
  invitations.push(invited, accepted, interviewed);

  pipelines.set(need.id, [
    {
      candidate_id: first.candidate_id,
      stage: 'shortlist',
      note: 'Отличное покрытие, обсудить на синке найма',
      invitation_id: null,
      updated_at: '2026-10-06T08:00:00Z',
    },
    {
      candidate_id: second.candidate_id,
      stage: 'invited',
      note: null,
      invitation_id: invited.id,
      updated_at: invited.sent_at,
    },
    {
      candidate_id: third.candidate_id,
      stage: 'accepted',
      note: 'Контакты раскрыты, договориться о звонке',
      invitation_id: accepted.id,
      updated_at: accepted.decided_at ?? nowIso(),
    },
    {
      candidate_id: fourth.candidate_id,
      stage: 'interview',
      note: 'Техинтервью в четверг 16:00',
      invitation_id: interviewed.id,
      updated_at: '2026-10-07T09:00:00Z',
    },
  ]);

  // junior-потребность: один кандидат в шорт-листе, чтобы доска не была пустой
  const needJunior = needById('need-2');
  const juniorTop = rankCandidates(needJunior)[0];
  if (juniorTop) {
    pipelines.set(needJunior.id, [
      {
        candidate_id: juniorTop.candidate_id,
        stage: 'shortlist',
        note: null,
        invitation_id: null,
        updated_at: '2026-10-06T12:00:00Z',
      },
    ]);
  }

  // Входящие приглашения кандидата (persona judge-candidate)
  invitations.push(
    makeInvitation({
      side: 'candidate',
      title: 'Backend-разработчик в платёжный сервис',
      message:
        'Ваши подтверждённые узлы закрывают ядро нашей потребности: проектирование API, слой данных, диагностика. Зовём пообщаться — без резюме и тестовых.',
      salary_from: 180_000,
      salary_to: 260_000,
      contact_method: null,
      explanation: {
        basis: 'subgraph',
        text: 'Совпадение 7 из 8 узлов подграфа: B1–B4 подтверждены свежими результатами теста.',
      },
      sent_at: '2026-10-07T09:30:00Z',
      expires_at: daysFromNow(13),
    }),
    makeInvitation({
      side: 'candidate',
      company_name: 'Цифровой контур',
      title: 'Инженер интеграций',
      message: 'Понравился ваш профиль связности: мосты в frontend — то, что нужно нашей команде.',
      salary_from: 150_000,
      salary_to: 210_000,
      contact_method: 'email',
      status: 'accepted',
      sent_at: '2026-10-03T10:00:00Z',
      viewed_at: '2026-10-03T11:00:00Z',
      decided_at: '2026-10-03T12:00:00Z',
      expires_at: daysFromNow(9),
      explanation: { basis: 'grade_profile', text: 'Профиль middle подтверждён, Q2.' },
      company_contacts: {
        company_name: 'Цифровой контур',
        contact_method: 'email',
        contact_email: 'talent@kontur-demo.ru',
        contact_phone: null,
      },
    }),
    makeInvitation({
      side: 'candidate',
      company_name: 'Личный профиль: А. Орлов',
      title: 'Бэкенд в стартап',
      salary_from: 90_000,
      salary_to: 140_000,
      contact_method: null,
      status: 'declined',
      sent_at: '2026-09-28T10:00:00Z',
      viewed_at: '2026-09-28T12:00:00Z',
      decided_at: '2026-09-29T09:00:00Z',
      expires_at: '2026-10-12T10:00:00Z',
      decline_reason_code: 'salary',
      decline_text: 'Вилка ниже моей планки.',
      explanation: { basis: 'profile_strength', text: 'Сильный профиль без привязки к подграфу.' },
    }),
  );
}

/**
 * Сид выполняется лениво при первом CRM-запросе: mock.ts и mock-crm.ts
 * импортируют друг друга, и на этапе инициализации модуля GRAPHS ещё в TDZ.
 */
let seeded = false;
function ensureSeeded(): void {
  if (seeded) return;
  seeded = true;
  seedFunnel();
}

function invitationById(id: string): MockInvitation {
  const found = invitations.find((inv) => inv.id === id);
  if (!found) throw notFound('Приглашение не найдено');
  return found;
}

function employerInvitationOut(inv: MockInvitation): InvitationOut {
  const { side: _side, candidate_id, company_contacts: _contacts, company_name: _name, ...rest } = inv;
  return {
    ...rest,
    candidate_pseudonym: candidate_id ? candidateById(candidate_id).pseudonym : null,
  };
}

function candidateInvitationOut(inv: MockInvitation): InvitationOut {
  const { side: _side, candidate_id: _cid, company_contacts: _contacts, ...rest } = inv;
  // contact_method скрыт до принятия
  return { ...rest, contact_method: inv.status === 'accepted' ? inv.contact_method : null };
}

function pipelineOut(needId: string): NeedPipelineOut {
  rankCandidates(needById(needId));
  const entries = pipelines.get(needId) ?? [];
  const items: PipelineItem[] = entries.map((entry) => {
    const cand = candidateById(entry.candidate_id);
    const inv = entry.invitation_id
      ? invitations.find((i) => i.id === entry.invitation_id)
      : undefined;
    const accepted = inv?.status === 'accepted';
    return {
      candidate_id: cand.id,
      pseudonym: cand.pseudonym,
      stage: entry.stage,
      note: entry.note,
      invitation_id: entry.invitation_id,
      invitation_status: (inv?.status ?? null) as InvitationStatus | null,
      contacts_available: Boolean(accepted),
      full_name_masked: accepted ? cand.full_name : null,
      score: rankedScore(needId, cand.id),
      integrity_level: cand.integrity_level,
      updated_at: entry.updated_at,
    };
  });
  const columns = PIPELINE_STAGE_ORDER.map((stage) => ({
    stage,
    items: items.filter((item) => item.stage === stage),
  }));
  return { need_id: needId, columns, total: items.length };
}

// ---------------------------------------------------------------------------
// Сигналы целостности: демо-сессия для разработки раннера и страниц работодателя
// ---------------------------------------------------------------------------

/** Единственная «завершённая» сессия фикстур: GET integrity отвечает только по ней. */
export const DEMO_INTEGRITY_SESSION_ID = 'sess-demo-1';

function demoIntegrityReport(): IntegrityReport {
  return {
    level: 'medium',
    flags: [
      {
        code: 'large_paste',
        title: 'Крупная вставка',
        count: 1,
        detail: 'вставка 312 символов в ответ на задание B3 (порог 200)',
      },
      {
        code: 'tab_blur',
        title: 'Уход с вкладки',
        count: 2,
        detail: 'суммарно 47 секунд вне вкладки теста (порог 5 секунд за раз)',
      },
    ],
    events_total: 57,
    computed_at: nowIso(),
  };
}

const telegram = {
  linked: false,
  code: null as string | null,
  issuedAt: 0,
};

function telegramStatus(): TelegramLinkStatus {
  // Имитация: через ~12 секунд после выдачи кода «пользователь нажал Start в боте»
  if (!telegram.linked && telegram.code && Date.now() - telegram.issuedAt > 12_000) {
    telegram.linked = true;
  }
  return {
    linked: telegram.linked,
    username_masked: telegram.linked ? '@r***y' : null,
    linked_at: telegram.linked ? new Date(telegram.issuedAt + 12_000).toISOString() : null,
    digest_enabled: telegram.linked,
  };
}

// ---------------------------------------------------------------------------
// Telegram Mini App: дев-актор по чату `dev:<chat>` (контракт интегратора)
// ---------------------------------------------------------------------------

/** Чаты дев-режима мини-аппа: какой актор отвечает на /tma/* в фикстурах. */
const TMA_EMPLOYER_CHAT = '200500';
const TMA_MEMBER_CHAT = '300500';
const TMA_UNLINKED_CHAT = '0';

let tmaChat = '100500';

/** Вызывается TMA-клиентом перед каждым мок-запросом: актор = чат из `dev:<chat>`. */
export function setMockTmaChat(chat: string): void {
  tmaChat = chat;
}

function tmaMe(): TmaMe {
  if (tmaChat === TMA_EMPLOYER_CHAT) {
    return { kind: 'employer', company_name: company.name, role: company.my_role };
  }
  if (tmaChat === TMA_MEMBER_CHAT) {
    return { kind: 'member', company_name: company.name, role: 'hr' };
  }
  return { kind: 'candidate', pseudonym: 'B-7F3A', grade: 'middle' };
}

/**
 * Пути /tma/* — контракт мини-аппа: та же бизнес-логика, что и веб-эндпоинты,
 * поэтому обработчики ДЕЛЕГИРУЮТ существующим ключам диспетчера (не дублируем).
 * Чат 0 имитирует непривязанный телеграм → 403 tma_not_linked.
 */
function tmaRequestMock<T>(key: string, body?: unknown): T | typeof UNHANDLED {
  if (tmaChat === TMA_UNLINKED_CHAT) {
    throw forbidden('tma_not_linked', 'Сначала привяжите бота: /start с кодом из настроек');
  }

  if (key === 'GET /tma/me') return tmaMe() as T;

  if (key === 'GET /tma/invitations') {
    return mockCrmRequest<T>('GET', '/candidates/me/invitations');
  }

  const invitationAction = /^POST \/tma\/invitations\/([^/]+)\/(accept|decline)$/.exec(key);
  if (invitationAction) {
    return mockCrmRequest<T>('POST', `/invitations/${invitationAction[1]}/${invitationAction[2]}`, body);
  }

  if (key === 'GET /tma/needs') {
    const active = needs.filter((n) => n.status === 'active').map((n) => ({ ...n }));
    return { items: active, total: active.length } as T;
  }

  const pipelineGet = /^GET \/tma\/needs\/([^/]+)\/pipeline$/.exec(key);
  if (pipelineGet) {
    return mockCrmRequest<T>('GET', `/needs/${pipelineGet[1]}/pipeline`);
  }

  const pipelineMove = /^PATCH \/tma\/needs\/([^/]+)\/pipeline\/([^/]+)$/.exec(key);
  if (pipelineMove) {
    return mockCrmRequest<T>('PATCH', `/needs/${pipelineMove[1]}/pipeline/${pipelineMove[2]}`, body);
  }

  return UNHANDLED;
}

// ---------------------------------------------------------------------------
// Диспетчер
// ---------------------------------------------------------------------------

/** Обрабатывает CRM-пути; UNHANDLED — путь не из этого модуля. */
export function mockCrmRequest<T>(
  method: string,
  path: string,
  body?: unknown,
): T | typeof UNHANDLED {
  ensureSeeded();
  const key = `${method.toUpperCase()} ${path}`;

  // --- Telegram Mini App ---
  if (path.startsWith('/tma/')) return tmaRequestMock<T>(key, body);

  // --- компания ---
  if (key === 'GET /employers/me/company') return { ...company } as T;
  if (key === 'PUT /employers/me/company') {
    const patch = body as CompanyUpdate;
    if (patch.kind === 'personal' && members.filter((m) => m.status !== 'revoked').length > 1) {
      throw conflict('company_has_members', 'Нельзя сделать профиль личным: в компании есть участники');
    }
    Object.assign(company, patch, { updated_at: nowIso() });
    return { ...company } as T;
  }

  if (key === 'GET /employers/me/company/members') {
    return { items: members.map((m) => ({ ...m })), total: members.length } as T;
  }

  if (key === 'POST /employers/me/company/members/invite') {
    const invite = body as MemberInviteBody;
    if (company.kind === 'personal') {
      throw conflict('company_kind_personal', 'Личный профиль: сначала сделайте компанию корпоративной');
    }
    const contact = invite.channel === 'email' ? invite.email : invite.telegram;
    if (!contact) throw badRequest('Укажите контакт для выбранного канала приглашения');
    const duplicate = members.some(
      (m) =>
        m.status !== 'revoked' &&
        (m.email_masked === maskContact(contact) || m.telegram_masked === maskContact(contact)),
    );
    if (duplicate) throw conflict('member_already_invited', 'Этот контакт уже приглашён в компанию');
    memberSeq += 1;
    const member: MemberOut = {
      id: `mem-${memberSeq}`,
      role: invite.role,
      status: 'invited',
      full_name_masked: invite.full_name ? maskContact(invite.full_name) : null,
      email_masked: invite.channel === 'email' ? maskContact(contact) : null,
      telegram_masked: invite.channel === 'telegram' ? maskContact(contact) : null,
      invite_channel: invite.channel,
      has_account: false,
      invited_at: nowIso(),
      accepted_at: null,
    };
    members.push(member);
    company.members_count = members.length;
    const token = `orb-${hex(24)}`;
    inviteTokens.set(token, member.id);
    const result: MemberInviteResult = {
      member,
      invite_token: token,
      invite_url: `${window.location.origin}/employer/invite/${token}`,
    };
    return result as T;
  }

  const memberDelete = /^DELETE \/employers\/me\/company\/members\/([^/]+)$/.exec(key);
  if (memberDelete) {
    const member = members.find((m) => m.id === memberDelete[1]);
    if (!member) throw notFound('Участник не найден');
    if (member.role === 'owner') throw conflict('owner_not_revocable', 'Владельца нельзя отозвать');
    member.status = 'revoked';
    return undefined as T;
  }

  const publicInvite = /^GET \/employers\/company\/invites\/([^/]+)$/.exec(key);
  if (publicInvite) {
    const memberId = inviteTokens.get(decodeURIComponent(publicInvite[1] ?? ''));
    const member = members.find((m) => m.id === memberId);
    if (!member || member.status !== 'invited') throw notFound('Приглашение не найдено или уже использовано');
    const info: PublicInviteInfo = { company_name: company.name, role: member.role, status: member.status };
    return info as T;
  }

  if (key === 'POST /employers/company/invites/accept') {
    const { token } = body as { token: string };
    const memberId = inviteTokens.get(token);
    const member = members.find((m) => m.id === memberId);
    if (!member || member.status !== 'invited') throw notFound('Приглашение не найдено или уже использовано');
    member.status = 'active';
    member.has_account = true;
    member.accepted_at = nowIso();
    inviteTokens.delete(token);
    return { ...member } as T;
  }

  // --- потребности ---
  if (key === 'GET /needs') {
    return { items: needs.map((n) => ({ ...n })), total: needs.length } as T;
  }
  if (key === 'POST /needs') {
    const create = body as NeedCreateBody;
    if (!create.salary_from || !create.salary_to || create.salary_from > create.salary_to) {
      throw badRequest('Вилка обязательна: «от» не больше «до», обе суммы больше нуля');
    }
    needSeq += 1;
    const need: NeedOut = {
      id: `need-${needSeq}`,
      title: create.title,
      raw_text: create.raw_text ?? null,
      first_months_tasks: create.first_months_tasks ?? null,
      specialization: create.specialization,
      grade: create.grade,
      stack_tags: create.stack_tags ?? [],
      industry_code: create.industry_code ?? null,
      salary_from: create.salary_from,
      salary_to: create.salary_to,
      format: create.format ?? null,
      status: 'active',
      created_at: nowIso(),
      updated_at: nowIso(),
    };
    needs.unshift(need);
    return { ...need } as T;
  }

  const needOne = /^(GET|PUT) \/needs\/([^/]+)$/.exec(key);
  if (needOne) {
    const need = needById(needOne[2] ?? '');
    if (needOne[1] === 'PUT') {
      Object.assign(need, body as Partial<NeedOut>, { id: need.id, updated_at: nowIso() });
    }
    return { ...need } as T;
  }

  const needCandidates = /^GET \/needs\/([^/]+)\/candidates$/.exec(key);
  if (needCandidates) {
    const need = needById(needCandidates[1] ?? '');
    const items = rankCandidates(need);
    const out: NeedCandidatesOut = { need_id: need.id, basis: 'subgraph', items, total: items.length };
    return out as T;
  }

  const needPipeline = /^(GET|POST) \/needs\/([^/]+)\/pipeline$/.exec(key);
  if (needPipeline) {
    const need = needById(needPipeline[2] ?? '');
    if (needPipeline[1] === 'GET') return pipelineOut(need.id) as T;
    const { candidate_id, note } = body as { candidate_id: string; note?: string };
    const entries = pipelines.get(need.id) ?? [];
    if (entries.some((e) => e.candidate_id === candidate_id)) {
      throw conflict('pipeline_entry_exists', 'Кандидат уже в воронке этой потребности');
    }
    entries.push({
      candidate_id: candidateById(candidate_id).id,
      stage: 'shortlist',
      note: note ?? null,
      invitation_id: null,
      updated_at: nowIso(),
    });
    pipelines.set(need.id, entries);
    return pipelineOut(need.id) as T;
  }

  const pipelinePatch = /^PATCH \/needs\/([^/]+)\/pipeline\/([^/]+)$/.exec(key);
  if (pipelinePatch) {
    const need = needById(pipelinePatch[1] ?? '');
    const entry = (pipelines.get(need.id) ?? []).find((e) => e.candidate_id === pipelinePatch[2]);
    if (!entry) throw notFound('Кандидат не найден в воронке найма');
    const { stage, comment } = body as { stage: PipelineStage; comment?: string };
    const needsAccepted: PipelineStage[] = ['interview', 'offer', 'hired'];
    const inv = entry.invitation_id ? invitations.find((i) => i.id === entry.invitation_id) : null;
    if (needsAccepted.includes(stage) && inv?.status !== 'accepted') {
      throw conflict(
        'pipeline_transition_invalid',
        'Интервью, оффер и наём доступны только после принятого приглашения',
      );
    }
    if (stage === 'invited' || stage === 'accepted') {
      throw conflict('pipeline_transition_invalid', 'Эти стадии ставит сервис приглашений');
    }
    entry.stage = stage;
    if (comment) entry.note = comment;
    entry.updated_at = nowIso();
    return pipelineOut(need.id) as T;
  }

  // --- приглашения ---
  if (key === 'POST /invitations') {
    const create = body as InvitationCreateBody;
    const cand = candidateById(create.candidate_id);
    const open = invitations.some(
      (i) =>
        i.side === 'employer' &&
        i.candidate_id === cand.id &&
        (i.status === 'sent' || i.status === 'viewed'),
    );
    if (open) throw conflict('invitation_already_open', 'У кандидата уже есть открытое приглашение от вас');
    const need = create.need_id ? needById(create.need_id) : null;
    const inv = makeInvitation({
      side: 'employer',
      need_id: need?.id ?? null,
      candidate_id: cand.id,
      title: create.title ?? need?.title ?? null,
      message: create.message ?? null,
      salary_from: create.salary_from,
      salary_to: create.salary_to,
      contact_method: create.contact_method ?? 'telegram',
      explanation: need
        ? {
            basis: 'subgraph',
            text:
              rankCandidates(need).find((r) => r.candidate_id === cand.id)?.explanation.text ?? '',
          }
        : {},
    });
    invitations.push(inv);
    if (need) {
      const entries = pipelines.get(need.id) ?? [];
      const entry = entries.find((e) => e.candidate_id === cand.id);
      if (entry) {
        entry.stage = 'invited';
        entry.invitation_id = inv.id;
        entry.updated_at = nowIso();
      } else {
        entries.push({
          candidate_id: cand.id,
          stage: 'invited',
          note: null,
          invitation_id: inv.id,
          updated_at: nowIso(),
        });
      }
      pipelines.set(need.id, entries);
    }
    const result: InvitationCreateResult = {
      invitation: employerInvitationOut(inv),
      below_salary_floor: cand.salary_floor !== null && create.salary_to < cand.salary_floor,
    };
    return result as T;
  }

  if (key === 'GET /employers/me/invitations') {
    const items = invitations.filter((i) => i.side === 'employer').map(employerInvitationOut);
    return { items: items.reverse(), total: items.length } as T;
  }

  if (key === 'GET /candidates/me/invitations') {
    const items = invitations.filter((i) => i.side === 'candidate').map(candidateInvitationOut);
    return { items, total: items.length } as T;
  }

  const invitationAction = /^POST \/invitations\/([^/]+)\/(view|accept|decline)$/.exec(key);
  if (invitationAction) {
    const inv = invitationById(invitationAction[1] ?? '');
    const action = invitationAction[2];
    if (action === 'view') {
      if (inv.status === 'sent') {
        inv.status = 'viewed';
        inv.viewed_at = nowIso();
      }
      return candidateInvitationOut(inv) as T;
    }
    if (inv.status === 'accepted' || inv.status === 'declined') {
      throw conflict('invitation_closed', 'Приглашение уже закрыто');
    }
    if (inv.status === 'expired') throw conflict('invitation_expired', 'Срок приглашения истёк');
    if (action === 'accept') {
      inv.status = 'accepted';
      inv.decided_at = nowIso();
      const result: InvitationAcceptResult = {
        invitation: candidateInvitationOut(inv),
        company_contacts: inv.company_contacts,
      };
      return result as T;
    }
    const decline = (body ?? {}) as InvitationDeclineBody;
    inv.status = 'declined';
    inv.decided_at = nowIso();
    inv.decline_reason_code = decline.reason_code ?? null;
    inv.decline_text = decline.text ?? null;
    return candidateInvitationOut(inv) as T;
  }

  const invitationContacts = /^GET \/invitations\/([^/]+)\/contacts$/.exec(key);
  if (invitationContacts) {
    const inv = invitationById(invitationContacts[1] ?? '');
    if (inv.status !== 'accepted' || !inv.candidate_id) {
      throw forbidden('forbidden', 'Контакты доступны только после принятого приглашения');
    }
    const cand = candidateById(inv.candidate_id);
    const short = cand.pseudonym.slice(2).toLowerCase();
    const contacts: CandidateContacts = {
      pseudonym: cand.pseudonym,
      full_name: cand.full_name,
      email: `dev-${short}@fsp.local`,
      phone: `+7 900 ${short.slice(0, 3)}-${short.slice(1)}`,
      telegram: `@dev_${short}`,
    };
    return contacts as T;
  }

  const invitationGet = /^GET \/invitations\/([^/]+)$/.exec(key);
  if (invitationGet) {
    const inv = invitationById(invitationGet[1] ?? '');
    return (inv.side === 'employer' ? employerInvitationOut(inv) : candidateInvitationOut(inv)) as T;
  }

  // --- телеметрия целостности сессии теста ---
  const integrityEvents = /^POST \/assessment\/sessions\/([^/]+)\/events$/.exec(key);
  if (integrityEvents) {
    // Батч принимаем от любой сессии: раннер команды может слать события до появления API
    const events = (body as IntegrityEventsBody | undefined)?.events ?? [];
    return { accepted: events.length } as T;
  }

  const integrityGet = /^GET \/assessment\/sessions\/([^/]+)\/integrity$/.exec(key);
  if (integrityGet) {
    if (integrityGet[1] !== DEMO_INTEGRITY_SESSION_ID) {
      throw notFound('Сессия не найдена или ещё не завершена');
    }
    return { ...demoIntegrityReport(), computed_at: nowIso() } as T;
  }

  // --- телеграм-бот ---
  if (key === 'GET /auth/telegram/me') return telegramStatus() as T;
  if (key === 'POST /auth/telegram/link-token') {
    telegram.code = `ORB-${hex(4).toUpperCase()}-${hex(2).toUpperCase()}`;
    telegram.issuedAt = Date.now();
    telegram.linked = false;
    const token: TelegramLinkToken = {
      code: telegram.code,
      deep_link: `https://t.me/OrbitaFspBot?start=link_${telegram.code.replaceAll('-', '')}`,
      expires_at: new Date(Date.now() + 10 * 60 * 1000).toISOString(),
    };
    return token as T;
  }

  return UNHANDLED;
}
