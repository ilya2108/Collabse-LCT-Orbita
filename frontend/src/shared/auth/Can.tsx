import type { ReactNode } from 'react';
import { useAuth } from './AuthContext';
import type { AppRole } from './roles';

interface CanProps {
  /** Показать детей, если у пользователя есть хотя бы одна из ролей. */
  roles: AppRole[];
  fallback?: ReactNode;
  children: ReactNode;
}

/**
 * Точечный RBAC-гейт для элементов UI: пользователь не видит кнопку, которую не может
 * нажать. Фронтовый RBAC — только UX-слой, сервер дублирует все проверки.
 */
export function Can({ roles, fallback = null, children }: CanProps): ReactNode {
  const { hasRole } = useAuth();
  return hasRole(...roles) ? children : fallback;
}
