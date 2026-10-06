import { motion, useReducedMotion } from 'motion/react';
import type { ReactNode } from 'react';
import { errorMessage, isApiError } from '@/shared/api/errors';
import { Illustration, type IllustrationName } from '@/shared/illustrations';
import { cn } from '@/shared/lib/cn';
import { springs } from '@/shared/lib/motion';
import { Button } from '@/shared/ui/button';
import { Skeleton } from '@/shared/ui/skeleton';

/**
 * Состояния «пусто / ошибка / загрузка»: у каждого состояния есть следующий шаг
 * (CTA или «Повторить»). Иллюстрации — собственный реестр shared/illustrations.
 * Ошибка API показывается человекочитаемым текстом backend + код обращения
 * (request_id), без стектрейсов.
 */

export { Illustration };
export type { IllustrationName };

export interface EmptyStateProps {
  illustration?: IllustrationName;
  title: string;
  description?: ReactNode;
  /** 1 primary + до 1 secondary CTA. */
  actions?: ReactNode;
  /** Внутри виджетов/ячеек: SVG 96px, без description. */
  compact?: boolean;
  className?: string;
}

export function EmptyState({
  illustration = 'box-empty',
  title,
  description,
  actions,
  compact = false,
  className,
}: EmptyStateProps): ReactNode {
  const reduced = useReducedMotion() ?? false;
  return (
    <div
      className={cn(
        'flex flex-col items-center text-center',
        compact ? 'gap-2 py-4' : 'gap-3 py-10',
        className,
      )}
    >
      <motion.div
        initial={reduced ? { opacity: 0 } : { opacity: 0, y: 12, scale: 0.96 }}
        animate={reduced ? { opacity: 1 } : { opacity: 1, y: 0, scale: 1 }}
        transition={reduced ? { duration: 0.2 } : springs.bouncy}
      >
        <Illustration name={illustration} height={compact ? 96 : 160} />
      </motion.div>
      <p className="text-base font-semibold text-balance">{title}</p>
      {description && !compact ? (
        <div className="max-w-[460px] text-sm text-muted-foreground">{description}</div>
      ) : null}
      {actions ? (
        <div className="mt-1 flex flex-wrap items-center justify-center gap-2">{actions}</div>
      ) : null}
    </div>
  );
}

export interface ErrorStateProps {
  error: unknown;
  onRetry?: () => void;
  title?: string;
  compact?: boolean;
}

/** Ошибка загрузки: сюжет error, текст без стектрейса, request_id мелко. */
export function ErrorState({
  error,
  onRetry,
  title = 'Не удалось загрузить данные',
  compact = false,
}: ErrorStateProps): ReactNode {
  const requestId = isApiError(error) ? error.requestId : null;
  return (
    <div
      role="alert"
      className={cn('flex flex-col items-center text-center', compact ? 'gap-2 py-4' : 'gap-3 py-10')}
    >
      <Illustration name="error-broken" height={compact ? 96 : 140} />
      <p className="text-base font-semibold text-balance">{title}</p>
      <p className="max-w-[460px] text-sm text-muted-foreground">{errorMessage(error)}</p>
      {requestId ? (
        <p className="text-xs text-muted-foreground">Код обращения: {requestId}</p>
      ) : null}
      {onRetry ? (
        <Button className="mt-1" onClick={onRetry}>
          Повторить
        </Button>
      ) : null}
    </div>
  );
}

interface LoadingStateProps {
  rows?: number;
  /** Обернуть в карточку (для страниц); false — голый скелетон. */
  card?: boolean;
  className?: string;
}

/** Первичная загрузка: скелетон-строки с shimmer, не спиннер. */
export function LoadingState({ rows = 6, card = true, className }: LoadingStateProps): ReactNode {
  const body = (
    <div className="flex flex-col gap-3" aria-hidden="true">
      <Skeleton className="h-4 w-1/3" />
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-4" style={{ width: `${100 - ((i * 13) % 30)}%` }} />
      ))}
    </div>
  );
  return card ? (
    <div className={cn('rounded-lg border bg-card p-5 shadow-card', className)}>{body}</div>
  ) : (
    <div className={className}>{body}</div>
  );
}
