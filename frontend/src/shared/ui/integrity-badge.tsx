import type { ReactNode } from 'react';
import type { IntegrityFlag, IntegrityLevel } from '@/shared/api/types';
import type { SemanticStatus } from '@/shared/config/tokens';
import { StatusBadge } from '@/shared/ui/status-badge';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/ui/tooltip';

/**
 * Бейдж сигналов целостности сессии теста. Формулировки намеренно нейтральные:
 * индекс — повод посмотреть внимательнее, а не вердикт «списал». level=null
 * (данных нет) — бейдж не рендерится вовсе, вызывающему коду проверка не нужна.
 * Цвета — только семантическими токенами success/warning/danger (тинт-фон + deep-текст).
 */

export const INTEGRITY_LEVEL_LABELS: Record<IntegrityLevel, string> = {
  high: 'Сессия без аномалий',
  medium: 'Есть особенности сессии',
  low: 'Требует внимания',
};

const INTEGRITY_LEVEL_STATUS: Record<IntegrityLevel, SemanticStatus> = {
  high: 'success',
  medium: 'warning',
  low: 'danger',
};

const LEVEL_TOOLTIP: Record<IntegrityLevel, string> = {
  high: 'Автоматические сигналы сессии теста в норме: без крупных вставок, долгих уходов с вкладки и подозрительно быстрых ответов.',
  medium: 'Один-два автоматических сигнала (например, крупная вставка или уход с вкладки). Это не вердикт — повод уточнить на интервью.',
  low: 'Три и более автоматических сигнала за сессию. Рекомендуем проверить ключевые навыки на интервью.',
};

export interface IntegrityBadgeProps {
  /** null/undefined — данных нет, бейдж не показывается. */
  level: IntegrityLevel | null | undefined;
  /** Флаги из GET integrity — перечисляются в тултипе; без них — общее описание уровня. */
  flags?: IntegrityFlag[];
  size?: 'sm' | 'md';
  className?: string;
}

export function IntegrityBadge({ level, flags, size = 'sm', className }: IntegrityBadgeProps): ReactNode {
  if (!level) return null;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex w-fit cursor-default" tabIndex={0}>
          <StatusBadge size={size} status={INTEGRITY_LEVEL_STATUS[level]} className={className}>
            {INTEGRITY_LEVEL_LABELS[level]}
          </StatusBadge>
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-80">
        <div className="grid gap-1.5 py-0.5 text-left">
          {flags && flags.length > 0 ? (
            <ul className="grid list-none gap-1">
              {flags.map((flag) => (
                <li key={flag.code}>
                  {flag.title}
                  {flag.count > 1 ? ` — ×${flag.count}` : ''}
                  {flag.detail ? `: ${flag.detail}` : ''}
                </li>
              ))}
            </ul>
          ) : (
            <p>{LEVEL_TOOLTIP[level]}</p>
          )}
          <p className="text-sidebar-muted">
            Индекс считается по событиям сессии (вставки, уходы с вкладки, тайминги);
            содержимое ответов не анализируется.
          </p>
        </div>
      </TooltipContent>
    </Tooltip>
  );
}
