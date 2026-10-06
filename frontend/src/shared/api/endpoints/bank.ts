import { api } from '../client';

/**
 * Банк кандидатов работодателя (`GET /matching/bank`): все кандидаты с категорией
 * и опубликованным профилем — только псевдонимы, без ПДн; контакты раскрываются
 * исключительно после принятого приглашения. Типы объявлены локально: контракт
 * используется только страницей банка (общий types.ts не трогаем).
 */

export interface BankCandidate {
  candidate_id: string;
  pseudonym: string;
  specialization_code: string;
  grade: string;
  q_level: number;
  shape: string;
  /** Дата присвоения категории — «грейд подтверждён тогда-то». */
  grade_confirmed_at: string;
  /** Подтверждённый заданиями стек (до 8 тегов). */
  stack_confirmed: string[];
}

export interface BankPage {
  items: BankCandidate[];
  total: number;
  limit: number;
  offset: number;
}

export interface BankFilters {
  specialization?: string;
  grade?: string;
  limit?: number;
  offset?: number;
}

export function fetchCandidateBank(
  filters: BankFilters,
  signal?: AbortSignal,
): Promise<BankPage> {
  return api.get<BankPage>('/matching/bank', {
    query: {
      specialization: filters.specialization,
      grade: filters.grade,
      limit: filters.limit,
      offset: filters.offset,
    },
    signal,
  });
}
