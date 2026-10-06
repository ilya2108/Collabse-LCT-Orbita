import { toastError, toastSuccess } from '@/shared/lib/toast';

/**
 * Обратная связь API-слоя вне React-дерева (клиент, кэши react-query):
 * тонкая обёртка над sonner-тостами из shared/lib/toast — message из ответа
 * сервера + request_id для обращения в поддержку собирает toastError.
 */

/** Показать ошибку API пользователю. */
export function notifyApiError(error: unknown, title = 'Не удалось выполнить действие'): void {
  toastError(error, { title });
}

export function notifySuccess(message: string, description?: string): void {
  toastSuccess(message, description);
}
