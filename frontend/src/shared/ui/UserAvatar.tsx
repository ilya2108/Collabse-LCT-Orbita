import type { ReactNode } from 'react';
import { cn } from '@/shared/lib/cn';
import { Avatar, AvatarFallback } from '@/shared/ui/avatar';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/ui/tooltip';

/**
 * Аватар-инициалы (псевдоним кандидата, компания) на shadcn Avatar:
 * детерминированный фон по hash имени — не «мигает» между рендерами.
 * Цвета — только классы-токены (hex в tsx запрещены, см. globals.css): тёмные оттенки
 * палитры, белые инициалы ≥ 4.5:1 на каждом. Пустое имя — нейтральный muted-фон.
 * Контракт прежний: fullName/size/tooltip.
 */

const AVATAR_CLASSES = [
  'bg-dark',
  'bg-deep',
  'bg-primary-deep',
  'bg-status-progress-deep',
  'bg-chart-4',
  'bg-status-warning-deep',
] as const;

function initialsOf(fullName: string): string {
  const parts = fullName.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  const first = parts[0]?.charAt(0) ?? '';
  const second = parts[1]?.charAt(0) ?? '';
  return (first + second).toUpperCase() || '?';
}

function colorClassOf(fullName: string): string {
  let hash = 0;
  for (const char of fullName) hash = (hash * 31 + char.charCodeAt(0)) % 997;
  return AVATAR_CLASSES[hash % AVATAR_CLASSES.length] ?? AVATAR_CLASSES[0];
}

interface UserAvatarProps {
  fullName: string | null | undefined;
  size?: number;
  /** Показывать тултип с полным именем (для канбан-карточек). */
  tooltip?: boolean;
}

export function UserAvatar({ fullName, size = 24, tooltip = true }: UserAvatarProps): ReactNode {
  const name = fullName?.trim() || 'Не назначен';
  const avatar = (
    <Avatar style={{ width: size, height: size }}>
      <AvatarFallback
        className={cn(
          'font-semibold',
          fullName ? cn(colorClassOf(name), 'text-white') : 'bg-muted text-muted-foreground',
        )}
        style={{ fontSize: Math.round(size * 0.42) }}
      >
        {fullName ? initialsOf(name) : '—'}
      </AvatarFallback>
    </Avatar>
  );
  if (!tooltip) return avatar;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex" aria-label={name}>
          {avatar}
        </span>
      </TooltipTrigger>
      <TooltipContent>{name}</TooltipContent>
    </Tooltip>
  );
}
