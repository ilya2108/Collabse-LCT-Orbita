import { api } from '../client';

/**
 * Вакансии: публичный список опубликованных (без ПДн), идемпотентный отклик кандидата,
 * вакансии своей компании со счётчиком откликов. Типы — локально: контракт модуля
 * `vacancies` бэкенда, в общий types.ts не выносим (файл в чужой зоне).
 */

export interface VacancyPublic {
  id: string;
  title: string;
  company_name: string;
  specialization_code: string | null;
  grade: string | null;
  salary_from: number;
  salary_to: number;
  format: string | null;
  description: string | null;
  /** Размер подграфа связанной потребности — отклик оценят по этим узлам. */
  need_nodes_count: number | null;
  /** Текущий кандидат уже откликался. */
  applied: boolean;
}

export interface VacancyMine {
  id: string;
  title: string;
  specialization_code: string | null;
  grade: string | null;
  salary_from: number;
  salary_to: number;
  format: string | null;
  status: string;
  description: string | null;
  need_nodes_count: number | null;
  applications_count: number;
  published_at: string | null;
}

export interface VacancyApplication {
  id: string;
  vacancy_id: string;
  status: string;
  sent_at: string;
}

/** Формат работы (контракт поля `vacancy.format`). */
export const VACANCY_FORMAT_LABELS: Record<string, string> = {
  remote: 'Удалёнка',
  hybrid: 'Гибрид',
  office: 'Офис',
};

export function vacancyFormatLabel(code: string | null | undefined): string {
  if (!code) return '';
  return VACANCY_FORMAT_LABELS[code] ?? code;
}

/** Вилка «от–до» по-русски: «от 90 000 до 140 000 ₽». */
export function salaryRange(from: number, to: number): string {
  return `от ${from.toLocaleString('ru-RU')} до ${to.toLocaleString('ru-RU')} ₽`;
}

export function fetchVacancies(
  signal?: AbortSignal,
): Promise<{ items: VacancyPublic[]; total: number }> {
  return api.get('/vacancies', { signal });
}

export function fetchMyVacancies(
  signal?: AbortSignal,
): Promise<{ items: VacancyMine[]; total: number }> {
  return api.get('/vacancies/mine', { signal });
}

/** Идемпотентно: повторный вызов возвращает уже существующий отклик. */
export function applyToVacancy(vacancyId: string): Promise<VacancyApplication> {
  return api.post<VacancyApplication>(`/vacancies/${vacancyId}/apply`);
}
