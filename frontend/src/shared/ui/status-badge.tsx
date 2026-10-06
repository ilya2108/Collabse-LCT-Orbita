import type { LucideIcon } from 'lucide-react';
import type { CSSProperties, ReactNode } from 'react';
import { cn } from '@/shared/lib/cn';
import { colorText, colorTint, type SemanticStatus } from '@/shared/config/tokens';

/**
 * StatusBadge: фон tint, текст deep, слева точка 6px core-цвета или иконка —
 * статус никогда не только цветом. Для произвольного цвета (например, слоя графа)
 * — color-mix формулы colorTint/colorText из tokens.ts.
 */

export interface StatusBadgeProps {
  /** Семантические тройки из tokens.ts. */
  status?: SemanticStatus;
  /** ИЛИ произвольный hex-цвет (слой графа, цвет из API). */
  color?: string;
  /** Иконка 12px вместо точки. */
  icon?: LucideIcon;
  children: ReactNode;
  size?: 'sm' | 'md';
  className?: string;
}

const STATUS_CLASSES: Record<SemanticStatus, { bg: string; text: string; dot: string }> = {
  draft: { bg: 'bg-status-draft-tint', text: 'text-status-draft-deep', dot: 'bg-status-draft' },
  progress: { bg: 'bg-status-progress-tint', text: 'text-status-progress-deep', dot: 'bg-status-progress' },
  success: { bg: 'bg-status-success-tint', text: 'text-status-success-deep', dot: 'bg-status-success' },
  warning: { bg: 'bg-status-warning-tint', text: 'text-status-warning-deep', dot: 'bg-status-warning' },
  danger: { bg: 'bg-status-danger-tint', text: 'text-status-danger-deep', dot: 'bg-status-danger' },
  brand: { bg: 'bg-status-brand-tint', text: 'text-status-brand-deep', dot: 'bg-status-brand' },
};

export function StatusBadge({
  status,
  color,
  icon: Icon,
  children,
  size = 'md',
  className,
}: StatusBadgeProps): ReactNode {
  const semantic = status ? STATUS_CLASSES[status] : null;
  const custom: CSSProperties | undefined =
    !semantic && color ? { backgroundColor: colorTint(color), color: colorText(color) } : undefined;
  const dotStyle: CSSProperties | undefined = !semantic && color ? { backgroundColor: color } : undefined;
  return (
    <span
      className={cn(
        'inline-flex w-fit shrink-0 items-center gap-1.5 whitespace-nowrap rounded-sm font-medium',
        size === 'sm' ? 'px-1.5 py-px text-[11px]' : 'px-2 py-0.5 text-xs',
        semantic ? [semantic.bg, semantic.text] : !color && 'bg-muted text-muted-foreground',
        className,
      )}
      style={custom}
    >
      {Icon ? (
        <Icon className="size-3 shrink-0" aria-hidden="true" />
      ) : (
        <span
          className={cn('size-1.5 shrink-0 rounded-full', semantic?.dot)}
          style={dotStyle}
          aria-hidden="true"
        />
      )}
      <span className="min-w-0 truncate">{children}</span>
    </span>
  );
}
