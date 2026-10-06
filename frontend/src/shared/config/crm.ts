import type { SemanticStatus } from '@/shared/config/tokens';
import type {
  CompanyKind,
  DeclineReason,
  InvitationStatus,
  MemberRole,
  MemberStatus,
  NeedGrade,
  NeedStatus,
  PipelineStage,
} from '@/shared/api/types';

/**
 * Словари мини-CRM: русские подписи и семантические статусы для стадий пайплайна,
 * приглашений, ролей участников компании и грейдов. Единая точка правды для страниц
 * работодателя и кандидата; порядок стадий — как отдаёт backend.
 */

export const PIPELINE_STAGE_ORDER: PipelineStage[] = [
  'shortlist',
  'invited',
  'accepted',
  'interview',
  'offer',
  'hired',
  'declined',
];

export const PIPELINE_STAGE_LABELS: Record<PipelineStage, string> = {
  shortlist: 'Шорт-лист',
  invited: 'Приглашён',
  accepted: 'Принял',
  interview: 'Интервью',
  offer: 'Оффер',
  hired: 'Нанят',
  declined: 'Отказ',
};

export const PIPELINE_STAGE_STATUS: Record<PipelineStage, SemanticStatus> = {
  shortlist: 'draft',
  invited: 'progress',
  accepted: 'brand',
  interview: 'progress',
  offer: 'warning',
  hired: 'success',
  declined: 'danger',
};

/**
 * Куда карточку можно перевести кнопками UI. invited/accepted ставит только сервис
 * приглашений; interview/offer/hired доступны после принятого приглашения
 * (иначе backend ответит 409 pipeline_transition_invalid).
 */
export const PIPELINE_MANUAL_TARGETS: Record<PipelineStage, PipelineStage[]> = {
  shortlist: ['declined'],
  invited: ['declined'],
  accepted: ['interview', 'declined'],
  interview: ['offer', 'declined'],
  offer: ['hired', 'declined'],
  hired: [],
  declined: [],
};

export const INVITATION_STATUS_LABELS: Record<InvitationStatus, string> = {
  sent: 'Отправлено',
  viewed: 'Просмотрено',
  accepted: 'Принято',
  declined: 'Отклонено',
  expired: 'Истекло',
};

export const INVITATION_STATUS_STATUS: Record<InvitationStatus, SemanticStatus> = {
  sent: 'progress',
  viewed: 'brand',
  accepted: 'success',
  declined: 'danger',
  expired: 'draft',
};

export const DECLINE_REASON_LABELS: Record<DeclineReason, string> = {
  salary: 'Не устраивает вилка',
  not_relevant: 'Не мой профиль',
  busy: 'Сейчас не ищу',
  other: 'Другое',
};

export const MEMBER_ROLE_LABELS: Record<MemberRole, string> = {
  owner: 'Владелец',
  hr: 'HR',
  hiring_manager: 'Нанимающий менеджер',
};

export const MEMBER_STATUS_LABELS: Record<MemberStatus, string> = {
  invited: 'Приглашён',
  active: 'Активен',
  revoked: 'Отозван',
};

export const MEMBER_STATUS_STATUS: Record<MemberStatus, SemanticStatus> = {
  invited: 'progress',
  active: 'success',
  revoked: 'draft',
};

export const COMPANY_KIND_LABELS: Record<CompanyKind, string> = {
  personal: 'Личный профиль',
  corporate: 'Корпоративный профиль',
};

export const GRADE_LABELS: Record<NeedGrade, string> = {
  junior: 'Junior',
  middle: 'Middle',
  senior: 'Senior',
};

export const NEED_STATUS_LABELS: Record<NeedStatus, string> = {
  draft: 'Черновик',
  active: 'Активна',
  archived: 'В архиве',
};

export const NEED_STATUS_STATUS: Record<NeedStatus, SemanticStatus> = {
  draft: 'draft',
  active: 'success',
  archived: 'draft',
};

/** Канал связи в приглашении (contact_method: telegram | email) — подпись для UI. */
export const CONTACT_METHOD_LABELS: Record<string, string> = {
  telegram: 'Telegram',
  email: 'эл. почта',
};

export function contactMethodLabel(code: string | null | undefined): string {
  if (!code) return '';
  return CONTACT_METHOD_LABELS[code] ?? code;
}

/** Имя телеграм-бота системы (deep-link показываем из API, имя — для текстов UI). */
export const TELEGRAM_BOT_NAME = '@OrbitaFspBot';
