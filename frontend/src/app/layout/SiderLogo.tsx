import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { cn } from '@/shared/lib/cn';

/**
 * Шапка сайдбара: официальный логотип ФСП (брендбук, вариант со светлым текстом
 * для тёмных подложек — public/brand/fsp-logo-light.svg) + название продукта
 * «ФСП.Орбита» с подзаголовком роли. В свёрнутом режиме — компактный знак-заглушка
 * «Орбиты» (favicon, горизонтальный логотип в 64px не помещается).
 */
export function SiderLogo({
  collapsed,
  to,
  subtitle,
}: {
  collapsed: boolean;
  to: string;
  subtitle?: string;
}): ReactNode {
  return (
    <Link
      to={to}
      aria-label="ФСП.Орбита — на главную"
      className={cn(
        'flex h-14 shrink-0 items-center gap-3 text-white outline-none',
        'focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-sidebar',
        collapsed ? 'justify-center px-0' : 'px-4',
      )}
    >
      {collapsed ? (
        <img src="/favicon.svg" alt="" width={28} height={28} className="block" />
      ) : (
        <>
          <img
            src="/brand/fsp-logo-light.svg"
            alt="Федерация спортивного программирования"
            className="block h-8 w-auto shrink-0"
          />
          <span className="h-6 w-px shrink-0 bg-white/15" aria-hidden="true" />
          <span className="flex min-w-0 flex-col leading-tight">
            <span className="whitespace-nowrap text-sm font-semibold">ФСП.Орбита</span>
            {subtitle ? (
              <span className="truncate text-[10px] text-sidebar-muted">{subtitle}</span>
            ) : null}
          </span>
        </>
      )}
    </Link>
  );
}
