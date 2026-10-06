import { api } from '../client';
import type {
  AiSummaryOut,
  CompanyOut,
  CompanyUpdate,
  MemberInviteBody,
  MemberInviteResult,
  MemberOut,
  NeedCandidatesOut,
  NeedCreateBody,
  NeedOut,
  NeedPipelineOut,
  PipelineStage,
  PublicInviteInfo,
} from '../types';

/**
 * Эндпоинты кабинета работодателя: компания и участники, потребности,
 * подборка и пайплайн (контракты агента ядра, CLAUDE.md «Контракты API»).
 */

// --- компания ---

/** Моя компания; при первом обращении создаётся личная (kind=personal). */
export function fetchMyCompany(signal?: AbortSignal): Promise<CompanyOut> {
  return api.get<CompanyOut>('/employers/me/company', { signal });
}

/** Частичный PUT; 409 company_has_members при corporate→personal с участниками. */
export function updateMyCompany(body: CompanyUpdate): Promise<CompanyOut> {
  return api.put<CompanyOut>('/employers/me/company', { body });
}

export function fetchCompanyMembers(
  signal?: AbortSignal,
): Promise<{ items: MemberOut[]; total: number }> {
  return api.get('/employers/me/company/members', { signal });
}

/** Токен из ответа показывается один раз; 409 company_kind_personal | member_already_invited. */
export function inviteMember(body: MemberInviteBody): Promise<MemberInviteResult> {
  return api.post<MemberInviteResult>('/employers/me/company/members/invite', { body });
}

/** 204; 409 owner_not_revocable для владельца. */
export function revokeMember(memberId: string): Promise<void> {
  return api.delete<void>(`/employers/me/company/members/${memberId}`);
}

/** ПУБЛИЧНЫЙ (без auth): информация для страницы принятия и телеграм-бота. */
export function fetchPublicInvite(token: string, signal?: AbortSignal): Promise<PublicInviteInfo> {
  return api.get<PublicInviteInfo>(`/employers/company/invites/${encodeURIComponent(token)}`, {
    signal,
  });
}

/** Принять приглашение в компанию: привязывает аккаунт, дописывает роль employer. */
export function acceptCompanyInvite(token: string): Promise<MemberOut> {
  return api.post<MemberOut>('/employers/company/invites/accept', { body: { token } });
}

// --- потребности ---

export function fetchNeeds(signal?: AbortSignal): Promise<{ items: NeedOut[]; total: number }> {
  return api.get('/needs', { signal });
}

export function createNeed(body: NeedCreateBody): Promise<NeedOut> {
  return api.post<NeedOut>('/needs', { body });
}

export function updateNeed(needId: string, body: Partial<NeedCreateBody> & { status?: NeedOut['status'] }): Promise<NeedOut> {
  return api.put<NeedOut>(`/needs/${needId}`, { body });
}

// --- подборка и пайплайн ---

/** Ранжированные кандидаты под потребность с объяснением «почему здесь». */
export function fetchNeedCandidates(
  needId: string,
  signal?: AbortSignal,
): Promise<NeedCandidatesOut> {
  return api.get<NeedCandidatesOut>(`/needs/${needId}/candidates`, { signal });
}

/** Картина по кандидатам на потребность глазами ИИ (`GET /employers/needs/{id}/selection-summary`):
 * оверфит, недобор, возможные компромиссы. Генерация LLM — до пары минут (дальше кэш);
 * 404 — подборки ещё нет. Запрашивается только по явному клику: бережём лимит GigaChat. */
export function fetchSelectionSummary(
  needId: string,
  signal?: AbortSignal,
): Promise<AiSummaryOut> {
  return api.get<AiSummaryOut>(
    `/employers/needs/${encodeURIComponent(needId)}/selection-summary`,
    { signal },
  );
}

export function fetchNeedPipeline(needId: string, signal?: AbortSignal): Promise<NeedPipelineOut> {
  return api.get<NeedPipelineOut>(`/needs/${needId}/pipeline`, { signal });
}

/** Добавить в шорт-лист; 409 pipeline_entry_exists при повторе. */
export function addToPipeline(
  needId: string,
  body: { candidate_id: string; note?: string },
): Promise<NeedPipelineOut> {
  return api.post<NeedPipelineOut>(`/needs/${needId}/pipeline`, { body });
}

/** Перевод стадии; 409 pipeline_transition_invalid до принятого приглашения. */
export function movePipelineStage(
  needId: string,
  candidateId: string,
  body: { stage: PipelineStage; comment?: string },
): Promise<NeedPipelineOut> {
  return api.patch<NeedPipelineOut>(`/needs/${needId}/pipeline/${candidateId}`, { body });
}
