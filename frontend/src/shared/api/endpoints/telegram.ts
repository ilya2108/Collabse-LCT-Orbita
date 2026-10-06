import { api } from '../client';
import type { TelegramLinkStatus, TelegramLinkToken } from '../types';

/**
 * Привязка телеграм-бота ФСП.Орбита и недельный дайджест.
 * Контракт backend: модуль tgbot (`/auth/telegram/*`).
 */

/** Статус привязки аккаунта к боту. */
export function fetchTelegramStatus(signal?: AbortSignal): Promise<TelegramLinkStatus> {
  return api.get<TelegramLinkStatus>('/auth/telegram/me', { signal });
}

/** Одноразовый код и deep-link на бота (жмём «Привязать»). */
export function createTelegramLinkToken(): Promise<TelegramLinkToken> {
  return api.post<TelegramLinkToken>('/auth/telegram/link-token');
}
