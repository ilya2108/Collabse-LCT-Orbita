import { api } from '../client';
import type {
  CandidateContacts,
  InvitationAcceptResult,
  InvitationCreateBody,
  InvitationCreateResult,
  InvitationDeclineBody,
  InvitationOut,
} from '../types';

/**
 * Приглашения с зарплатой «от–до» (контракты агента ядра): работодатель отправляет,
 * кандидат принимает/отклоняет; контакты раскрываются только после принятия.
 */

/** 409 invitation_already_open | invitation_quota | candidate_daily_limit. */
export function createInvitation(body: InvitationCreateBody): Promise<InvitationCreateResult> {
  return api.post<InvitationCreateResult>('/invitations', { body });
}

export function fetchEmployerInvitations(
  signal?: AbortSignal,
): Promise<{ items: InvitationOut[]; total: number }> {
  return api.get('/employers/me/invitations', { signal });
}

export function fetchCandidateInvitations(
  signal?: AbortSignal,
): Promise<{ items: InvitationOut[]; total: number }> {
  return api.get('/candidates/me/invitations', { signal });
}

/** Кандидат открыл приглашение: sent → viewed (явная отметка, не автоматом). */
export function markInvitationViewed(id: string): Promise<InvitationOut> {
  return api.post<InvitationOut>(`/invitations/${id}/view`);
}

/** Принять: создаёт contact_grant, возвращает контакты компании; 409 invitation_closed|expired. */
export function acceptInvitation(id: string): Promise<InvitationAcceptResult> {
  return api.post<InvitationAcceptResult>(`/invitations/${id}/accept`);
}

export function declineInvitation(
  id: string,
  body: InvitationDeclineBody,
): Promise<InvitationOut> {
  return api.post<InvitationOut>(`/invitations/${id}/decline`, { body });
}

/** Работодателю после принятия (contact_grant); 403 до него. */
export function fetchInvitationContacts(
  id: string,
  signal?: AbortSignal,
): Promise<CandidateContacts> {
  return api.get<CandidateContacts>(`/invitations/${id}/contacts`, { signal });
}
