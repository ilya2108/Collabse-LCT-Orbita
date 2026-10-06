import { motion } from 'motion/react';
import { useMemo, type ReactNode } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { activeNavKey, type NavItem, type RoleNav } from '@/app/navigation';
import { cn } from '@/shared/lib/cn';
import { springs } from '@/shared/lib/motion';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/ui/tooltip';

/**
 * Навигация сайдбара по RoleNav: иконки lucide 18px, активный пункт — скользящая
 * подложка `layoutId` (springs.snappy) цвета sidebar-active + розовая полоска слева,
 * свёрнутый режим — иконки с Tooltip side="right". Пункты «скоро» — с бейджем.
 */

interface AppSidebarProps {
  nav: RoleNav;
  collapsed?: boolean;
  /** Уникальный суффикс layoutId — сайдбар и мобильный Sheet не конфликтуют. */
  layoutIdSuffix: string;
  /** Закрыть мобильный Sheet после перехода. */
  onNavigate?: () => void;
}

interface NavLinkItemProps {
  item: NavItem;
  active: boolean;
  collapsed: boolean;
  layoutId: string;
  onNavigate?: () => void;
}

function NavLinkItem({ item, active, collapsed, layoutId, onNavigate }: NavLinkItemProps): ReactNode {
  const Icon = item.icon;
  const link = (
    <Link
      to={item.path}
      onClick={onNavigate}
      aria-current={active ? 'page' : undefined}
      // в свёрнутом режиме видна только иконка (aria-hidden): имя ссылки — из aria-label,
      // Tooltip доступного имени не создаёт (WCAG 4.1.2 / 2.4.4)
      aria-label={collapsed ? item.label : undefined}
      className={cn(
        'relative flex min-h-10 items-center gap-3 rounded-md px-3 py-2 text-sm outline-none',
        'transition-colors duration-150 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-sidebar',
        active ? 'text-white' : 'text-sidebar-muted hover:bg-white/5 hover:text-white',
        collapsed && 'justify-center px-0',
      )}
    >
      {active ? (
        <motion.span
          layoutId={layoutId}
          transition={springs.snappy}
          className="absolute inset-0 rounded-md bg-sidebar-active"
          aria-hidden="true"
        >
          <span className="absolute inset-y-2 left-0 w-[3px] rounded-r-full bg-primary" />
        </motion.span>
      ) : null}
      <Icon className="relative z-10 size-[18px] shrink-0" aria-hidden="true" />
      {collapsed ? null : (
        <span className="relative z-10 flex min-w-0 flex-1 items-center gap-2">
          <span className="min-w-0 flex-1 truncate">{item.label}</span>
          {item.soon ? (
            <span className="rounded-sm bg-white/10 px-1.5 py-px text-[10px] font-medium uppercase tracking-wide text-sidebar-muted">
              скоро
            </span>
          ) : null}
        </span>
      )}
    </Link>
  );

  if (!collapsed) return link;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{link}</TooltipTrigger>
      <TooltipContent side="right">{item.label}</TooltipContent>
    </Tooltip>
  );
}

export function AppSidebar({
  nav,
  collapsed = false,
  layoutIdSuffix,
  onNavigate,
}: AppSidebarProps): ReactNode {
  const location = useLocation();
  const selectedKey = useMemo(() => activeNavKey(nav, location.pathname), [nav, location.pathname]);
  const layoutId = `nav-active-${layoutIdSuffix}`;

  return (
    <nav aria-label="Основная навигация" className="flex flex-col gap-3 px-2 py-3">
      {nav.sections.map((section) => (
        <div key={section.key} className="flex flex-col gap-0.5">
          {section.label && !collapsed ? (
            <p className="px-3 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wide text-sidebar-muted/70">
              {section.label}
            </p>
          ) : null}
          {section.label && collapsed ? (
            <span className="mx-auto my-1 h-px w-6 bg-white/10" aria-hidden="true" />
          ) : null}
          {section.items.map((item) => (
            <NavLinkItem
              key={item.key}
              item={item}
              active={selectedKey === item.key}
              collapsed={collapsed}
              layoutId={layoutId}
              onNavigate={onNavigate}
            />
          ))}
        </div>
      ))}
    </nav>
  );
}
