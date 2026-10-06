import { api } from '../client';
import type { ConsentBody, CurrentUser, SetRoleBody } from '../types';

/** Профиль, роли и согласия текущего пользователя (`GET /auth/me`). */
export function fetchMe(signal?: AbortSignal): Promise<CurrentUser> {
  return api.get<CurrentUser>('/auth/me', { signal });
}

/** Выбор роли при первом входе (`POST /auth/role`); 409 role_already_set — роль уже есть. */
export function setRole(body: SetRoleBody): Promise<CurrentUser> {
  return api.post<CurrentUser>('/auth/role', { body });
}

/** Согласие на обработку ПДн и (опционально) публикацию профиля (`POST /auth/consent`). */
export function giveConsent(body: ConsentBody): Promise<CurrentUser> {
  return api.post<CurrentUser>('/auth/consent', { body });
}

/** Отзыв согласия и удаление ПДн (`DELETE /auth/account`); на день 6 backend отвечает 501. */
export function deleteAccount(): Promise<void> {
  return api.delete<void>('/auth/account');
}
