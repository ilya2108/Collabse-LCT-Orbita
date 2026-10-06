import type { ApiErrorBody, ApiErrorDetail } from './types';

/**
 * Ошибка API в едином плоском формате backend `{code, message, details, request_id}`.
 * `message` — человекочитаемый русский текст, показывается в UI как есть;
 * `requestId` — для «кода обращения» в тостах и состояниях ошибки.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: ApiErrorDetail[];
  readonly requestId: string | null;

  constructor(
    status: number,
    code: string,
    message: string,
    details: ApiErrorDetail[] = [],
    requestId: string | null = null,
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
  }
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError;
}

const STATUS_FALLBACK_MESSAGES: Record<number, string> = {
  400: 'Некорректный запрос',
  401: 'Требуется вход в систему',
  403: 'Недостаточно прав для этого действия',
  404: 'Объект не найден',
  409: 'Конфликт данных: обновите страницу и повторите',
  413: 'Файл слишком большой',
  422: 'Данные не прошли проверку',
  429: 'Слишком много запросов, повторите позже',
  500: 'Внутренняя ошибка сервера',
  501: 'Эта функция ещё не реализована',
  502: 'Внешний сервис недоступен',
  503: 'Сервис временно недоступен',
};

function isApiErrorBody(value: unknown): value is ApiErrorBody {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as ApiErrorBody).code === 'string' &&
    typeof (value as ApiErrorBody).message === 'string'
  );
}

/** Собирает ApiError из HTTP-ответа: плоское тело backend или фолбэк по статусу. */
export function apiErrorFromResponse(
  status: number,
  body: unknown,
  requestIdHeader: string | null = null,
): ApiError {
  if (isApiErrorBody(body)) {
    const { code, message, details, request_id } = body;
    return new ApiError(status, code, message, details ?? [], request_id ?? requestIdHeader);
  }
  const message =
    STATUS_FALLBACK_MESSAGES[status] ?? `Не удалось выполнить запрос (HTTP ${status})`;
  return new ApiError(status, 'unknown_error', message, [], requestIdHeader);
}

/** Сетевая ошибка (fetch упал, backend не запущен, прокси недоступен). */
export function networkError(): ApiError {
  return new ApiError(
    0,
    'network_error',
    'Нет связи с сервером. Проверьте, что backend запущен, и повторите.',
  );
}

/** Человекочитаемое сообщение из любой ошибки — для тостов и состояний экрана. */
export function errorMessage(error: unknown, fallback = 'Что-то пошло не так'): string {
  if (isApiError(error)) return error.message;
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

/** Ошибка «функция ещё не реализована» (501 not_implemented) — для мягких заглушек дня 6. */
export function isNotImplemented(error: unknown): boolean {
  return isApiError(error) && (error.status === 501 || error.code === 'not_implemented');
}
