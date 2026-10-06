import type { SelectableRole, UserRole } from '@/shared/api/types';

/**
 * Ролевая модель «Орбиты ФСП» (CLAUDE.md «Keycloak и роли»): роль хранится в БД,
 * Keycloak только аутентифицирует. `candidate` и `employer` выбираются при первом
 * входе, `admin` назначается только в Keycloak.
 */
export const APP_ROLES = ['candidate', 'employer', 'admin'] as const satisfies readonly UserRole[];

export type AppRole = UserRole;

export const SELECTABLE_ROLES = ['candidate', 'employer'] as const satisfies readonly SelectableRole[];

export const ROLE_LABELS: Record<AppRole, string> = {
  candidate: 'Кандидат',
  employer: 'Работодатель',
  admin: 'Администратор',
};

/** Короткое описание роли для экрана выбора. */
export const ROLE_DESCRIPTIONS: Record<SelectableRole, string> = {
  candidate:
    'Подтверждаю навыки тестом и достижениями ФСП, получаю категорию и приглашения с зарплатой',
  employer: 'Описываю потребность, получаю объяснимую подборку и первым выхожу на контакт',
};

/** Порядок «старшинства» — для подписи в шапке, когда active_role не задана. */
const ROLE_PRIORITY: AppRole[] = ['admin', 'employer', 'candidate'];

export function isAppRole(value: string): value is AppRole {
  return (APP_ROLES as readonly string[]).includes(value);
}

export function isSelectableRole(value: string): value is SelectableRole {
  return (SELECTABLE_ROLES as readonly string[]).includes(value);
}

/** Пересечение ролей пользователя со списком разрешённых. */
export function hasAnyRole(userRoles: readonly AppRole[], allowed: readonly AppRole[]): boolean {
  return allowed.some((role) => userRoles.includes(role));
}

export function primaryRole(userRoles: readonly AppRole[]): AppRole | null {
  return ROLE_PRIORITY.find((role) => userRoles.includes(role)) ?? null;
}

/** Домашний маршрут роли: лэйаут определяется ролью. */
export function homePathFor(role: AppRole | null): string {
  switch (role) {
    case 'candidate':
      return '/candidate';
    case 'employer':
      return '/employer';
    case 'admin':
      return '/skills/graph/be';
    default:
      return '/choose-role';
  }
}
