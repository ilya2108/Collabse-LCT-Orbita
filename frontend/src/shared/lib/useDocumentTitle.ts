import { useEffect } from 'react';

export const APP_NAME = 'ФСП.Орбита';

/**
 * Заголовок вкладки «<Экран> — ФСП.Орбита» (WCAG 2.4.2 Page Titled): screen reader
 * и история браузера различают страницы SPA. Вызывается в PageHeader и на экранах
 * без него (вход, выбор роли, согласие, ошибки); при размонтировании возвращает прежний.
 */
export function useDocumentTitle(title: string | undefined): void {
  useEffect(() => {
    if (!title) return undefined;
    const previous = document.title;
    document.title = `${title} — ${APP_NAME}`;
    return () => {
      document.title = previous;
    };
  }, [title]);
}
