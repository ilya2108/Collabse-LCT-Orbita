import { apiErrorFromResponse, isApiError, networkError } from '@/shared/api/errors';
import { mockRequest } from '@/shared/api/mock';
import { setMockTmaChat } from '@/shared/api/mock-crm';
import type {
  InvitationAcceptResult,
  InvitationDeclineBody,
  InvitationOut,
  NeedOut,
  NeedPipelineOut,
  PipelineStage,
  TmaMe,
} from '@/shared/api/types';
import { env } from '@/shared/config/env';
import { resolveInitData } from './telegram';

/**
 * Тонкий fetch-клиент мини-аппа — НЕ общий shared/api/client (там Keycloak-токены):
 * авторизация каждого запроса — заголовок `X-Telegram-Init-Data` (initData из
 * Telegram.WebApp, в дев-режиме `dev:<chat>`), формат ошибок — тот же плоский
 * `{code, message, details, request_id}` → ApiError. 403 `tma_not_linked` экраны
 * показывают как «Привяжите бота» (isTmaNotLinked).
 *
 * В режиме фикстур (`npm run dev:mock`) запросы уходят в mock-crm: dev-чат из
 * initData выбирает актора (100500 — кандидат, 200500 — работодатель, 300500 —
 * участник-HR, 0 — непривязанный чат).
 */

async function tmaRequest<T>(method: string, path: string, body?: unknown): Promise<T> {
  const initData = resolveInitData();

  if (env.mockApi) {
    setMockTmaChat(initData.value.startsWith('dev:') ? initData.value.slice(4) : '');
    return mockRequest<T>(method, path, body);
  }

  const headers: Record<string, string> = {
    Accept: 'application/json',
    'X-Telegram-Init-Data': initData.value,
    'X-Request-Id': crypto.randomUUID(),
  };
  let payload: BodyInit | undefined;
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json; charset=utf-8';
    payload = JSON.stringify(body);
  }

  let response: Response;
  try {
    response = await fetch(`${env.apiUrl}${path}`, { method, headers, body: payload });
  } catch {
    throw networkError();
  }

  const text = await response.text();
  let parsed: unknown;
  try {
    parsed = text ? (JSON.parse(text) as unknown) : undefined;
  } catch {
    parsed = text;
  }
  if (!response.ok) {
    throw apiErrorFromResponse(response.status, parsed, response.headers.get('X-Request-Id'));
  }
  return parsed as T;
}

/** 403 tma_not_linked — чат не привязан к аккаунту: экран «Привяжите бота». */
export function isTmaNotLinked(error: unknown): boolean {
  return isApiError(error) && error.code === 'tma_not_linked';
}

/** Кто открыл мини-апп; по kind выбирается стартовый экран. */
export function fetchTmaMe(): Promise<TmaMe> {
  return tmaRequest<TmaMe>('GET', '/tma/me');
}

/** Приглашения кандидата — как GET /candidates/me/invitations. */
export function fetchTmaInvitations(): Promise<{ items: InvitationOut[]; total: number }> {
  return tmaRequest('GET', '/tma/invitations');
}

/** Принять приглашение: раскрывает контакты компании (как веб-принятие). */
export function acceptTmaInvitation(id: string): Promise<InvitationAcceptResult> {
  return tmaRequest<InvitationAcceptResult>('POST', `/tma/invitations/${id}/accept`, {});
}

export function declineTmaInvitation(
  id: string,
  body: InvitationDeclineBody,
): Promise<InvitationOut> {
  return tmaRequest<InvitationOut>('POST', `/tma/invitations/${id}/decline`, body);
}

/** Активные потребности компании актора (employer|member). */
export function fetchTmaNeeds(): Promise<{ items: NeedOut[]; total: number }> {
  return tmaRequest('GET', '/tma/needs');
}

/** Пайплайн потребности — колонки как в вебе (псевдонимы, контакты после accepted). */
export function fetchTmaPipeline(needId: string): Promise<NeedPipelineOut> {
  return tmaRequest<NeedPipelineOut>('GET', `/tma/needs/${needId}/pipeline`);
}

/** Перевод стадии — те же правила переходов; 409 pipeline_transition_invalid. */
export function moveTmaPipelineStage(
  needId: string,
  candidateId: string,
  stage: PipelineStage,
): Promise<NeedPipelineOut> {
  return tmaRequest<NeedPipelineOut>('PATCH', `/tma/needs/${needId}/pipeline/${candidateId}`, {
    stage,
  });
}
