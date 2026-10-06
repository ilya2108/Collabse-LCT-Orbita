/**
 * Типизированный доступ к Telegram WebApp SDK без npm-пакета: официальный скрипт
 * `telegram-web-app.js` подгружается динамически только на маршрутах /tma/*,
 * дальше работаем с `window.Telegram?.WebApp`. themeParams НАМЕРЕННО не используем —
 * мини-апп остаётся в палитре брендбука ФСП (токены globals.css).
 *
 * initData берётся из WebApp.initData; вне Telegram (обычный браузер) включается
 * дев-режим: строка `dev:<tg_chat_id>` из query-параметра `?dev_chat=` (по умолчанию
 * 100500) — backend принимает её только в APP_ENV=dev|test при TELEGRAM_BOT_MODE=stub.
 */

/** Кнопка «Назад» в шапке Telegram; вне Telegram недоступна. */
export interface TelegramBackButton {
  isVisible: boolean;
  show(): void;
  hide(): void;
  onClick(cb: () => void): void;
  offClick(cb: () => void): void;
}

/** Подмножество Telegram.WebApp, которое использует мини-апп. */
export interface TelegramWebApp {
  /** Сырая строка initData для заголовка X-Telegram-Init-Data; '' вне Telegram. */
  initData: string;
  /** Сообщить Telegram, что интерфейс готов (убирает плейсхолдер загрузки). */
  ready(): void;
  /** Развернуть мини-апп на всю высоту. */
  expand(): void;
  BackButton?: TelegramBackButton;
}

declare global {
  interface Window {
    Telegram?: { WebApp?: TelegramWebApp };
  }
}

const SDK_URL = 'https://telegram.org/js/telegram-web-app.js';
const SDK_TIMEOUT_MS = 1500;

export function getTelegramWebApp(): TelegramWebApp | null {
  return window.Telegram?.WebApp ?? null;
}

let sdkPromise: Promise<TelegramWebApp | null> | null = null;

/**
 * Подгрузить SDK один раз. null — скрипт не загрузился (нет сети, закрытый контур,
 * блокировщик): мини-апп продолжает работать в дев-режиме без ready()/expand().
 */
export function loadTelegramSdk(): Promise<TelegramWebApp | null> {
  sdkPromise ??= new Promise((resolve) => {
    if (getTelegramWebApp()) {
      resolve(getTelegramWebApp());
      return;
    }
    const script = document.createElement('script');
    const timer = window.setTimeout(() => {
      script.remove();
      resolve(null);
    }, SDK_TIMEOUT_MS);
    script.src = SDK_URL;
    script.async = true;
    script.onload = () => {
      window.clearTimeout(timer);
      resolve(getTelegramWebApp());
    };
    script.onerror = () => {
      window.clearTimeout(timer);
      script.remove();
      resolve(null);
    };
    document.head.appendChild(script);
  });
  return sdkPromise;
}

/** Чат дев-режима по умолчанию (фикстуры: кандидат). */
export const DEV_CHAT_DEFAULT = '100500';

export interface TmaInitData {
  /** Значение заголовка X-Telegram-Init-Data. */
  value: string;
  /** true — дев-режим `dev:<chat>` (открыто вне Telegram): показываем жёлтую плашку. */
  dev: boolean;
}

let cachedInitData: TmaInitData | null = null;

/**
 * initData мини-аппа. Кэшируется при первом вызове: query-параметр `?dev_chat=`
 * теряется при клиентской навигации, а initData на время сессии неизменен.
 */
export function resolveInitData(): TmaInitData {
  if (cachedInitData) return cachedInitData;
  const fromTelegram = getTelegramWebApp()?.initData;
  // Запасной путь без SDK: Telegram кладёт initData в hash как #tgWebAppData=<urlencoded>
  // (работает, даже если telegram-web-app.js не загрузился в WebView).
  // URLSearchParams декодирует значение ровно один раз — результат и есть исходная initData
  const fromHash = new URLSearchParams(window.location.hash.replace(/^#/, '')).get('tgWebAppData');
  const raw = fromTelegram || fromHash || '';
  if (raw) {
    cachedInitData = { value: raw, dev: false };
  } else {
    const chat = new URLSearchParams(window.location.search).get('dev_chat') || DEV_CHAT_DEFAULT;
    cachedInitData = { value: `dev:${chat}`, dev: true };
  }
  return cachedInitData;
}
