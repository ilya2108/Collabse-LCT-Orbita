/** Общие типы контракта API «Орбиты ФСП» (PLAN.md §7.5, CLAUDE.md «Контракты API»). */

/** Ответ-конверт списка (core/pagination.list_envelope). */
export interface ListEnvelope<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

/** Элемент `details` тела ошибки: ошибки валидации — `{field, code, message}`. */
export interface ApiErrorDetail {
  field?: string;
  code: string;
  message: string;
}

/** Единый ПЛОСКИЙ формат ошибки backend (core/errors.py). */
export interface ApiErrorBody {
  code: string;
  message: string;
  details?: ApiErrorDetail[] | null;
  request_id?: string | null;
}

/** Роли приложения: хранятся в БД (`app_user.roles[]`), admin — только из Keycloak. */
export type UserRole = 'candidate' | 'employer' | 'admin';

/** Выбираемые роли при первом входе (`POST /auth/role`). */
export type SelectableRole = 'candidate' | 'employer';

/** Блок согласий 152-ФЗ в `GET /auth/me`. */
export interface Consent {
  /** Когда дано согласие на обработку ПДн; null — ещё не дано. */
  pdn_at: string | null;
  /** Версия политики, под которой дано согласие (например «2026-10-06»). */
  policy_version: string | null;
  /** Когда дано согласие на публикацию профиля работодателям; null — профиль закрыт. */
  publish_at: string | null;
}

/** Текущий пользователь — `GET /auth/me`. */
export interface CurrentUser {
  id: string;
  email: string | null;
  email_verified: boolean;
  roles: UserRole[];
  active_role: UserRole | null;
  consent: Consent;
  created_at: string;
}

/** Тело `POST /auth/role`. */
export interface SetRoleBody {
  role: SelectableRole;
}

/** Тело `POST /auth/consent`. */
export interface ConsentBody {
  policy_version: string;
  publish: boolean;
}

// ---------------------------------------------------------------------------
// skills — справочник графов (PLAN.md §3.2)
// ---------------------------------------------------------------------------

export interface Industry {
  code: string;
  title: string;
}

export type SpecializationStatus = 'active' | 'preview';

/** Элемент `GET /skills/specializations`. */
export interface Specialization {
  code: string;
  title: string;
  /** `preview` — только граф, без банка шаблонов и теста. */
  status: SpecializationStatus;
  graph_version: string;
  node_count: number;
}

export type SkillLayerCode = 'D' | 'B' | 'C';

export interface SkillLayerInfo {
  code: SkillLayerCode;
  title: string;
}

/** Узел графа: id глобальный («be.D1»), code — внутри специализации («D1»). */
export interface SkillNode {
  id: string;
  code: string;
  layer: SkillLayerCode;
  title: string;
  description: string;
}

export type SkillEdgeKind = 'vertical' | 'lateral' | 'bridge';

export interface SkillEdge {
  source: string;
  target: string;
  kind: SkillEdgeKind;
}

/** `GET /skills/graph/{spec}`. Мосты ведут в узлы ДРУГИХ специализаций. */
export interface SkillGraph {
  specialization: {
    code: string;
    title: string;
    status: SpecializationStatus;
    graph_version: string;
  };
  layers: SkillLayerInfo[];
  nodes: SkillNode[];
  edges: SkillEdge[];
  bridges: SkillEdge[];
}

/** Элемент `GET /skills/grade-profiles/{spec}`. */
export interface GradeProfile {
  grade: string;
  target_nodes: string[];
  task_level: 1 | 2 | 3;
  min_active: number;
  min_q: number;
  constraints: Record<string, unknown>;
  budget: { min_items: number; max_items: number };
  demo: {
    target_nodes: string[];
    min_active: number;
    min_q: number;
    constraints: Record<string, unknown>;
  };
}

// ---------------------------------------------------------------------------
// candidates — минимум дня 6 (PLAN.md §7.4 candidate_profile)
// ---------------------------------------------------------------------------

/** `GET|PUT /candidates/me` (минимум дня 6; ПДн-поля шифруются на backend).
 * Поля — как в backend `candidates/schemas.py::CandidateMeOut`; собственные ПДн отдаются открыто. */
export interface CandidateProfile {
  id: string;
  /** Псевдоним вида «B-7F3A» — публичное имя до раскрытия контактов. */
  pseudonym: string;
  full_name: string | null;
  phone: string | null;
  telegram: string | null;
  city: string | null;
  /** Код отрасли из `GET /skills/industries`. */
  industry_code: string | null;
  /** Специализация из `GET /skills/specializations` — обязательна для теста на грейд. */
  specialization: string | null;
  /** Заявленный стек из анкеты — в ранжировании не участвует, показывается с пометкой. */
  stack_tags_declared: string[];
  stack_tags_confirmed: string[];
  experience_years: number | null;
  roles_declared: string[];
  soft_skills_declared: string[];
  /** «О себе» после санитайзера backend (в БД — about_sanitized). */
  about: string | null;
  salary_floor: number | null;
  privacy: CandidatePrivacy;
  /** Эффективный флаг «виден в банке» (privacy.searchable И есть категория). */
  searchable: boolean;
  fsp_id: string | null;
  fsp_linked_at: string | null;
  created_at: string;
  updated_at: string;
}

/** Настройки приватности кандидата (день 6 — только чтение). */
export interface CandidatePrivacy {
  searchable: boolean;
  show_city: boolean;
  show_fsp: boolean;
  show_accomplishments: boolean;
  hidden_company_ids: string[];
}

/** Тело `PUT /candidates/me` — частичное: переданные поля заменяют, null очищает. */
export interface CandidateProfileUpdate {
  full_name?: string | null;
  phone?: string | null;
  telegram?: string | null;
  city?: string | null;
  industry_code?: string | null;
  /** Код из `GET /skills/specializations`; null — сбросить. */
  specialization?: string | null;
  stack_tags_declared?: string[];
  experience_years?: number | null;
  roles_declared?: string[];
  soft_skills_declared?: string[];
  about?: string | null;
  salary_floor?: number | null;
  /** Настройки приватности: переданные ключи заменяют текущие; эффективный флаг
   * searchable профиля backend пересчитывает (privacy.searchable И категория есть). */
  privacy?: Partial<CandidatePrivacy>;
}

/** `GET /candidates/me/category` → категория; 404 `category_not_assigned` — ещё нет. */
export interface CandidateCategory {
  specialization: string;
  grade: string;
  grade_confirmed: boolean;
  q_level: number;
  computed_at: string;
}

/** Состояние подтверждения узла у кандидата (затухающие подтверждения, PLAN.md §3). */
export type CandidateNodeState = 'fresh' | 'fading' | 'expired' | 'off';

/** Элемент `GET /candidates/me/skill-states`. */
export interface CandidateSkillState {
  /** Глобальный id узла графа («be.B1»). */
  node_id: string;
  specialization_code: string;
  /** Активный уровень узла; 0 — не активирован. */
  active_level: number;
  /** Текущий вес подтверждения с учётом затухания. */
  w_now: number;
  state: CandidateNodeState;
}

/** `GET /candidates/me/skill-states` — только для роли candidate (иначе 403/404). */
export interface CandidateSkillStates {
  states: CandidateSkillState[];
}

// ---------------------------------------------------------------------------
// employers — компания, участники, приглашение HR (контракты агента ядра)
// ---------------------------------------------------------------------------

export type CompanyKind = 'personal' | 'corporate';
export type MemberRole = 'owner' | 'hr' | 'hiring_manager';
export type MemberStatus = 'invited' | 'active' | 'revoked';
export type InviteChannel = 'email' | 'telegram';

/** `GET|PUT /employers/me/company`; при первом обращении создаётся личная (kind=personal). */
export interface CompanyOut {
  id: string;
  kind: CompanyKind;
  name: string;
  inn: string | null;
  description: string | null;
  industry_code: string | null;
  website: string | null;
  contact_email: string | null;
  contact_phone: string | null;
  my_role: MemberRole;
  members_count: number;
  created_at: string;
  updated_at: string;
}

/** Тело `PUT /employers/me/company` — частичный PUT. */
export interface CompanyUpdate {
  name?: string;
  kind?: CompanyKind;
  inn?: string | null;
  description?: string | null;
  industry_code?: string | null;
  website?: string | null;
  contact_email?: string | null;
  contact_phone?: string | null;
}

/** Участник компании — ПДн только маскированные. */
export interface MemberOut {
  id: string;
  role: MemberRole;
  status: MemberStatus;
  full_name_masked: string | null;
  email_masked: string | null;
  telegram_masked: string | null;
  invite_channel: InviteChannel | null;
  /** false — приглашён без входа на платформу (например, HR через Telegram). */
  has_account: boolean;
  invited_at: string | null;
  accepted_at: string | null;
}

/** Тело `POST /employers/me/company/members/invite`. */
export interface MemberInviteBody {
  role: 'hr' | 'hiring_manager';
  channel: InviteChannel;
  full_name?: string;
  email?: string;
  telegram?: string;
}

/** Ответ инвайта: токен показывается ОДИН раз. */
export interface MemberInviteResult {
  member: MemberOut;
  invite_token: string;
  invite_url: string;
}

/** `GET /employers/company/invites/{token}` — публичный (страница принятия, телеграм-бот). */
export interface PublicInviteInfo {
  company_name: string;
  role: MemberRole;
  status: MemberStatus;
}

// ---------------------------------------------------------------------------
// needs — потребности работодателя
// ---------------------------------------------------------------------------

export type NeedGrade = 'junior' | 'middle' | 'senior';
export type NeedStatus = 'draft' | 'active' | 'archived';

/** `POST|GET|PUT /needs` — NeedOut. */
export interface NeedOut {
  id: string;
  title: string;
  raw_text: string | null;
  first_months_tasks: string | null;
  specialization: string;
  grade: NeedGrade;
  stack_tags: string[];
  industry_code: string | null;
  salary_from: number;
  salary_to: number;
  format: string | null;
  status: NeedStatus;
  created_at: string;
  updated_at: string;
}

/** Тело `POST /needs` (salary_from/to обязательны, from ≤ to > 0). */
export interface NeedCreateBody {
  title: string;
  raw_text?: string;
  first_months_tasks?: string;
  specialization: string;
  grade: NeedGrade;
  stack_tags?: string[];
  industry_code?: string;
  salary_from: number;
  salary_to: number;
  format?: string;
}

// ---------------------------------------------------------------------------
// matching — подборка и пайплайн (мини-CRM)
// ---------------------------------------------------------------------------

export type RankingBasis = 'subgraph' | 'grade_profile' | 'profile_strength';

export interface RankedExplanation {
  basis: RankingBasis;
  matched_nodes: string[];
  partial_nodes: string[];
  missing_nodes: string[];
  text: string;
}

/** Элемент `GET /needs/{id}/candidates`; несовпадающий грейд всегда ниже. */
export interface RankedCandidate {
  candidate_id: string;
  pseudonym: string;
  score: number;
  coverage: number;
  grade: NeedGrade;
  /** false — грейд заявлен, но не подтверждён тестом: бейдж и позиция ниже. */
  grade_confirmed: boolean;
  q_level: number | null;
  shape: string | null;
  /** Сигналы целостности последней завершённой сессии; null — кандидат ещё не тестировался. */
  integrity_level: IntegrityLevel | null;
  explanation: RankedExplanation;
}

export interface NeedCandidatesOut {
  need_id: string;
  basis: RankingBasis;
  items: RankedCandidate[];
  total: number;
}

/** Саммари от ИИ-консультанта (`GET /candidates/me/graph-summary` и
 * `GET /employers/needs/{id}/selection-summary`): человеческий разбор графа кандидата
 * или картины по кандидатам на потребность. Генерация LLM может идти до пары минут,
 * дальше ответ кэшируется бэкендом; 404 — данных для разбора ещё нет.
 * `source === 'rules'` — упрощённый разбор без LLM (fallback по правилам). */
export interface AiSummaryOut {
  /** Текст разбора; абзацы разделены пустой строкой (`\n\n`). */
  summary: string;
  source: 'llm' | 'rules';
  generated_at: string;
}

/** Элемент `GET /needs/{id}/pipeline` и `GET /needs/{id}/candidates`: уровень целостности
 * последней завершённой сессии теста кандидата; null — данных нет (бейдж не показывается). */
export type IntegrityLevel = 'high' | 'medium' | 'low';

export type PipelineStage =
  | 'shortlist'
  | 'invited'
  | 'accepted'
  | 'interview'
  | 'offer'
  | 'hired'
  | 'declined';

/** Карточка пайплайна; full_name_masked появляется только после accepted. */
export interface PipelineItem {
  candidate_id: string;
  pseudonym: string;
  stage: PipelineStage;
  note: string | null;
  invitation_id: string | null;
  invitation_status: InvitationStatus | null;
  contacts_available: boolean;
  full_name_masked: string | null;
  score: number | null;
  /** Сигналы целостности последней завершённой сессии; null — данных нет. */
  integrity_level: IntegrityLevel | null;
  updated_at: string;
}

export interface PipelineColumn {
  stage: PipelineStage;
  items: PipelineItem[];
}

/** `GET /needs/{need_id}/pipeline` — колонки в порядке стадий. */
export interface NeedPipelineOut {
  need_id: string;
  columns: PipelineColumn[];
  total: number;
}

// ---------------------------------------------------------------------------
// invitations — приглашения с зарплатой «от–до»
// ---------------------------------------------------------------------------

export type InvitationStatus = 'sent' | 'viewed' | 'accepted' | 'declined' | 'expired';
export type DeclineReason = 'salary' | 'not_relevant' | 'busy' | 'other';

/** InvitationOut: работодатель видит candidate_pseudonym, кандидат — company_name. */
export interface InvitationOut {
  id: string;
  need_id: string | null;
  candidate_pseudonym?: string | null;
  company_name?: string | null;
  title: string | null;
  message: string | null;
  salary_from: number;
  salary_to: number;
  /** Скрыт от кандидата до принятия. */
  contact_method: string | null;
  explanation: Record<string, unknown>;
  status: InvitationStatus;
  sent_at: string;
  viewed_at: string | null;
  decided_at: string | null;
  expires_at: string;
  decline_reason_code: DeclineReason | null;
  decline_text: string | null;
}

export interface InvitationCreateBody {
  candidate_id: string;
  need_id?: string;
  title?: string;
  message?: string;
  salary_from: number;
  salary_to: number;
  contact_method?: string;
}

/** Ответ `POST /invitations`: предупреждение «ниже планки» без суммы планки. */
export interface InvitationCreateResult {
  invitation: InvitationOut;
  below_salary_floor: boolean;
}

export interface CompanyContacts {
  company_name: string;
  contact_method: string | null;
  contact_email: string | null;
  contact_phone: string | null;
}

/** Ответ `POST /invitations/{id}/accept` — контакты компании раскрываются кандидату. */
export interface InvitationAcceptResult {
  invitation: InvitationOut;
  company_contacts: CompanyContacts;
}

export interface InvitationDeclineBody {
  reason_code?: DeclineReason;
  text?: string;
}

/** `GET /invitations/{id}/contacts` — работодателю после contact_grant. */
export interface CandidateContacts {
  pseudonym: string;
  full_name: string | null;
  email: string | null;
  phone: string | null;
  telegram: string | null;
}

// ---------------------------------------------------------------------------
// telegram — привязка бота ФСП.Орбита (контракт ожидает реализации backend)
// ---------------------------------------------------------------------------

/** `GET /telegram/me` — статус привязки аккаунта к боту. */
export interface TelegramLinkStatus {
  linked: boolean;
  username_masked: string | null;
  linked_at: string | null;
  /** Недельный дайджест: кандидату — вакансии, работодателю — кандидаты под потребности. */
  digest_enabled: boolean;
}

/** `POST /telegram/link-token` — одноразовый код и deep-link на бота. */
export interface TelegramLinkToken {
  code: string;
  deep_link: string;
  expires_at: string;
}

// ---------------------------------------------------------------------------
// assessment — телеметрия целостности сессии теста (контракт интегратора)
// ---------------------------------------------------------------------------

/** Виды событий телеметрии раннера теста. Тексты ответов в события НЕ попадают. */
export type IntegrityEventKind =
  | 'paste'
  | 'blur'
  | 'focus'
  | 'item_shown'
  | 'first_input'
  | 'answer_submitted';

/** Полезная нагрузка события: ТОЛЬКО длины и тайминги (ПДн-минимизация). */
export interface IntegrityEventPayload {
  item_id?: string;
  /** Длина (вставки — для paste; ответа — для answer_submitted), символов. */
  len?: number;
  /** Длительность: отсутствия на вкладке (focus) или работы над заданием. */
  duration_ms?: number;
  chars_typed?: number;
  chars_pasted?: number;
}

export interface IntegrityEvent {
  kind: IntegrityEventKind;
  /** Момент события на клиенте, ISO8601. */
  client_ts: string;
  payload: IntegrityEventPayload;
}

/** Тело `POST /assessment/sessions/{id}/events` — батч телеметрии. */
export interface IntegrityEventsBody {
  events: IntegrityEvent[];
}

/** Ответ 202 на батч телеметрии. */
export interface IntegrityEventsAccepted {
  accepted: number;
}

export type IntegrityFlagCode = 'large_paste' | 'tab_blur' | 'fast_answer' | 'no_edit';

export interface IntegrityFlag {
  code: IntegrityFlagCode;
  title: string;
  count: number;
  detail: string;
}

/** `GET /assessment/sessions/{id}/integrity`: high — 0 флагов, medium — 1–2, low — 3+. */
export interface IntegrityReport {
  level: IntegrityLevel;
  flags: IntegrityFlag[];
  events_total: number;
  computed_at: string;
}

// ---------------------------------------------------------------------------
// assessment — сессии теста на грейд (контракт интегратора 11.10)
// ---------------------------------------------------------------------------

/** Целевой грейд теста (`POST /assessment/sessions`). */
export type TargetGrade = 'junior' | 'middle' | 'senior';

/** Режим сессии: express — до 5 заданий по демо-профилю (стенд), full — одно задание
 * на каждый узел полного T-профиля грейда. Поле необязательно: без него backend
 * выбирает режим по TEST_DEMO_MODE. */
export type TestMode = 'express' | 'full';

/** Сессия теста: в API статус активной — «active» (в БД — in_progress). */
export interface TestSessionOut {
  id: string;
  status: string;
  specialization: string;
  target_grade: TargetGrade;
  /** null — сессия создана до появления режимов. */
  mode?: TestMode | null;
  items_total: number;
  current_index: number;
}

/** Вид задания: text — открытый ответ (textarea), choice — выбор одной опции. */
export type TestItemKind = 'text' | 'choice';

/** Опция закрытого задания; id — буква ключа A–D. */
export interface TestItemOption {
  id: string;
  text: string;
}

/** Задание БЕЗ эталонов и рубрик; options только у kind=choice. */
export interface TestItemBody {
  id: string;
  /** Полный id узла («be.B1»). */
  node_code: string;
  title: string;
  body_md: string;
  kind: TestItemKind;
  options: TestItemOption[] | null;
  /** Мягкий таймер: по нулю ответ только помечается, не блокируется. */
  time_limit_seconds: number | null;
}

/** `GET /assessment/sessions/{id}/items/current`. */
export interface TestItemCurrent {
  index: number;
  total: number;
  item: TestItemBody;
}

/** Тело ответа: text — для открытых, choice_id — для закрытых. */
export interface TestAnswerBody {
  text?: string;
  choice_id?: string;
}

/** Ответ принят; finished=true — заданий больше нет, пора на finish. */
export interface TestAnswerResult {
  accepted: boolean;
  finished: boolean;
  next_index: number | null;
}

/** Состояние свежести узла по PLAN.md §3.3 (off — узел не активирован). */
export type TestNodeState = 'fresh' | 'fading' | 'expired' | 'off';

export interface TestFinishNode {
  /** Полный id узла («be.B1»). */
  code: string;
  title: string;
  score: number;
  state: TestNodeState;
}

/** Итог `POST /assessment/sessions/{id}/finish`: грейдинг внутри (может занять минуты). */
export interface TestFinishResult {
  status: string;
  /** При провале — прежний грейд категории или null. */
  grade: string | null;
  /** «Этот тест пройден». */
  grade_confirmed: boolean;
  q_level: number;
  nodes: TestFinishNode[];
  /** null — телеметрии не было. */
  integrity: { level: IntegrityLevel; flags: IntegrityFlag[] } | null;
}

/** Итог сессии в разборе: strong — уверенно, passed — пройден, failed — не пройден. */
export type TestOutcome = 'strong' | 'passed' | 'failed';

/** Вид задания в разборе: skipped — кандидат не дал ответа. */
export type TestReviewItemKind = TestItemKind | 'skipped';

/** Критерий рубрики открытого задания: hit 1 — выполнен, 0.5 — частично, 0 — нет. */
export interface TestReviewCriterion {
  id: string;
  text: string;
  hit: number;
  /** Обоснование судьи (фрагмент ответа); может отсутствовать. */
  evidence?: string | null;
}

/** Задание в разборе последней сессии (`GET /assessment/sessions/last/review`). */
export interface TestReviewItem {
  /** Полный id узла («be.B1»). */
  node_code: string;
  node_title: string;
  title: string;
  kind: TestReviewItemKind;
  score: number;
  answered: boolean;
  /** Только для kind=choice: верен ли ответ; у остальных null. */
  correct: boolean | null;
  criteria: TestReviewCriterion[];
  /** Короткий совет «что улучшить»; может отсутствовать. */
  feedback_short?: string | null;
  /** true — LLM была недоступна, оценка эвристикой (фолбэк). */
  llm_fallback?: boolean;
}

/** Разбор последней завершённой сессии; 404 — завершённых сессий ещё нет. */
export interface TestReviewOut {
  session_id: string;
  finished_at: string;
  mode: TestMode | null;
  target_grade: TargetGrade;
  outcome: TestOutcome;
  /** Код специализации; в контракте 11.10 поля нет — UI делает фолбэк на категорию. */
  specialization?: string | null;
  nodes: TestFinishNode[];
  items: TestReviewItem[];
}

// ---------------------------------------------------------------------------
// tma — Telegram Mini App (контракт интегратора: заголовок X-Telegram-Init-Data)
// ---------------------------------------------------------------------------

/** Актор мини-аппа по tg_link: кандидат, владелец-работодатель или участник компании. */
export type TmaActorKind = 'candidate' | 'employer' | 'member';

/** `GET /tma/me` — кто открыл мини-апп; по kind выбирается стартовый экран. */
export interface TmaMe {
  kind: TmaActorKind;
  /** Для кандидата: псевдоним вида «B-7F3A». */
  pseudonym?: string | null;
  /** Для кандидата: подтверждённый грейд, если есть. */
  grade?: NeedGrade | null;
  /** Для employer|member: название компании. */
  company_name?: string | null;
  /** Для employer|member: роль в компании. */
  role?: MemberRole | null;
}
