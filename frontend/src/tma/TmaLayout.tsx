import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react';
import { Outlet } from 'react-router-dom';
import { LoadingState } from '@/shared/ui/states';
import {
  loadTelegramSdk,
  resolveInitData,
  type TelegramWebApp,
  type TmaInitData,
} from './telegram';

/**
 * Каркас Telegram Mini App (/tma/*): живёт ВНЕ RequireAuth и AppLayout — без
 * Keycloak и сайдбара. Шапка с логотипом ФСП на графите брендбука, контент в
 * колонке max-width 480, крупные тач-таргеты. Цвета — только токены палитры ФСП
 * (themeParams Telegram намеренно не применяются). При открытии вне Telegram —
 * жёлтая плашка дев-режима (initData `dev:<chat>` из `?dev_chat=`).
 */

interface TmaContextValue {
  /** Telegram.WebApp, если SDK загрузился (внутри Telegram); null — обычный браузер. */
  webApp: TelegramWebApp | null;
  initData: TmaInitData;
}

const TmaContext = createContext<TmaContextValue | null>(null);

export function useTma(): TmaContextValue {
  const ctx = useContext(TmaContext);
  if (!ctx) throw new Error('useTma используется вне TmaLayout');
  return ctx;
}

/**
 * Кнопка «Назад» в шапке Telegram на время жизни экрана (если доступна).
 * Вне Telegram экраны рисуют собственную кнопку возврата — хук тогда no-op.
 */
export function useTmaBackButton(onBack: () => void): void {
  const { webApp } = useTma();
  useEffect(() => {
    const back = webApp?.BackButton;
    if (!back) return undefined;
    back.onClick(onBack);
    back.show();
    return () => {
      back.offClick(onBack);
      back.hide();
    };
  }, [webApp, onBack]);
}

export function TmaLayout(): ReactNode {
  const [ctx, setCtx] = useState<TmaContextValue | null>(null);

  useEffect(() => {
    let cancelled = false;
    void loadTelegramSdk().then((webApp) => {
      if (cancelled) return;
      // ready() убирает плейсхолдер Telegram, expand() разворачивает на всю высоту
      webApp?.ready();
      webApp?.expand();
      setCtx({ webApp, initData: resolveInitData() });
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="flex min-h-screen flex-col bg-background">
      <header className="shrink-0 bg-dark">
        <div className="mx-auto flex h-14 w-full max-w-[480px] items-center gap-3 px-4 text-white">
          <img
            src="/brand/fsp-logo-light.svg"
            alt="Федерация спортивного программирования"
            className="block h-7 w-auto shrink-0"
          />
          <span className="h-5 w-px shrink-0 bg-white/15" aria-hidden="true" />
          <span className="flex min-w-0 flex-col leading-tight">
            <span className="whitespace-nowrap text-sm font-semibold">ФСП.Орбита</span>
            <span className="truncate text-[10px] text-sidebar-muted">мини-апп в Telegram</span>
          </span>
        </div>
      </header>

      {ctx?.initData.dev ? (
        <div className="shrink-0 bg-status-warning-tint">
          <p className="mx-auto w-full max-w-[480px] px-4 py-1.5 text-xs font-medium text-status-warning-deep">
            Режим разработки: вне Telegram, initData «{ctx.initData.value}»
          </p>
        </div>
      ) : null}

      <main className="mx-auto flex w-full max-w-[480px] flex-1 flex-col gap-4 px-4 py-4 pb-10">
        {ctx ? (
          <TmaContext.Provider value={ctx}>
            <Outlet />
          </TmaContext.Provider>
        ) : (
          <LoadingState rows={6} />
        )}
      </main>
    </div>
  );
}
