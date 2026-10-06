/**
 * Русские подписи для кодов домена: в интерфейсе не показываем сырые коды
 * (be/fe/data, fresh/off, strong/failed и т. п.) — только человеческие формулировки.
 * Названия специализаций дублируют title из graph.yaml бэкенда; при расхождении
 * приоритет у ответа API (listSpecializations), эти значения — синхронный фолбэк.
 */

export const SPECIALIZATION_LABELS: Record<string, string> = {
  be: 'Бэкенд',
  fe: 'Фронтенд',
  data: 'Данные',
};

export function specializationLabel(code: string | null | undefined): string {
  if (!code) return '';
  return SPECIALIZATION_LABELS[code] ?? code;
}

/** Грейды оставляем латиницей — это устоявшиеся названия уровней в ИТ-найме. */
export const GRADE_LABELS: Record<string, string> = {
  junior: 'Junior',
  middle: 'Middle',
  senior: 'Senior',
};

export function gradeLabel(code: string | null | undefined): string {
  if (!code) return '';
  return GRADE_LABELS[code.toLowerCase()] ?? code;
}

/** Состояние узла графа у кандидата (контракт fresh | fading | expired | off). */
export const NODE_STATE_LABELS: Record<string, string> = {
  fresh: 'подтверждён',
  fading: 'затухает',
  expired: 'устарел',
  off: 'не подтверждён',
};

export function nodeStateLabel(code: string | null | undefined): string {
  if (!code) return '';
  return NODE_STATE_LABELS[code] ?? code;
}

/** Итог сессии теста (outcome: strong | passed | failed). */
export const TEST_OUTCOME_LABELS: Record<string, string> = {
  strong: 'грейд подтверждён уверенно',
  passed: 'грейд подтверждён',
  failed: 'грейд не подтверждён',
};

export function testOutcomeLabel(code: string | null | undefined): string {
  if (!code) return '';
  return TEST_OUTCOME_LABELS[code] ?? code;
}

/** Режим теста. */
export const TEST_MODE_LABELS: Record<string, string> = {
  express: 'Экспресс',
  full: 'Полный',
};
