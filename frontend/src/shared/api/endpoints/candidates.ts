import { api } from '../client';
import type {
  AiSummaryOut,
  CandidateCategory,
  CandidateProfile,
  CandidateProfileUpdate,
  CandidateSkillStates,
} from '../types';

/** Профиль кандидата (`GET /candidates/me`); 404 — профиль ещё не создан. */
export function fetchMyProfile(signal?: AbortSignal): Promise<CandidateProfile> {
  return api.get<CandidateProfile>('/candidates/me', { signal });
}

/** Обновление профиля (`PUT /candidates/me`); 403 consent_required без согласия на ПДн. */
export function updateMyProfile(body: CandidateProfileUpdate): Promise<CandidateProfile> {
  return api.put<CandidateProfile>('/candidates/me', { body });
}

/** Моя категория (`GET /candidates/me/category`); 404 category_not_assigned — ещё нет. */
export function fetchMyCategory(signal?: AbortSignal): Promise<CandidateCategory> {
  return api.get<CandidateCategory>('/candidates/me/category', { signal });
}

/** Разбор моего графа человеческим языком (`GET /candidates/me/graph-summary`).
 * Генерация LLM — до пары минут (дальше кэш на бэке); 404 — данных графа ещё нет.
 * Запрашивается только по явному клику: бережём дневной лимит GigaChat. */
export function fetchMyGraphSummary(signal?: AbortSignal): Promise<AiSummaryOut> {
  return api.get<AiSummaryOut>('/candidates/me/graph-summary', { signal });
}

/** Состояния узлов графа у кандидата (`GET /candidates/me/skill-states`).
 * Доступно только роли candidate; 403/404 у остальных вызывающий код обрабатывает молча. */
export function fetchMySkillStates(signal?: AbortSignal): Promise<CandidateSkillStates> {
  return api.get<CandidateSkillStates>('/candidates/me/skill-states', { signal });
}
