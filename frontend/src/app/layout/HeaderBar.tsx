import { ChevronDown, LogOut, Menu, Settings, ShieldCheck } from 'lucide-react';
import { Fragment, useMemo, type ReactNode } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { breadcrumbsFor, type RoleNav } from '@/app/navigation';
import { useAuth } from '@/shared/auth/AuthContext';
import { ROLE_LABELS } from '@/shared/auth/roles';
import { env } from '@/shared/config/env';
import { Avatar, AvatarFallback } from '@/shared/ui/avatar';
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from '@/shared/ui/breadcrumb';
import { Button } from '@/shared/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu';

function initialsOf(email: string | null | undefined): string {
  const local = (email ?? '').split('@')[0] ?? '';
  return local.slice(0, 2).toUpperCase() || '…';
}

interface HeaderBarProps {
  nav: RoleNav;
  /** Бургер мобильного сайдбара (< lg): открывает Sheet в AppLayout. */
  showMenuButton?: boolean;
  onOpenMenu?: () => void;
}

/**
 * Хедер 56px: бургер (планшет), крошки, бейдж режима фикстур, меню профиля
 * (email, роль, настройки, выход).
 */
export function HeaderBar({ nav, showMenuButton = false, onOpenMenu }: HeaderBarProps): ReactNode {
  const { user, activeRole, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();

  const crumbs = useMemo(() => breadcrumbsFor(nav, location.pathname), [nav, location.pathname]);

  return (
    <header className="sticky top-0 z-20 flex h-14 shrink-0 items-center gap-3 border-b border-border bg-card px-4 lg:px-6">
      {showMenuButton ? (
        <Button variant="ghost" size="icon" aria-label="Открыть меню" onClick={onOpenMenu}>
          <Menu aria-hidden="true" />
        </Button>
      ) : null}

      {crumbs.length > 0 ? (
        <Breadcrumb className="hidden min-w-0 md:block">
          <BreadcrumbList className="flex-nowrap">
            {crumbs.map((crumb, index) => (
              <Fragment key={`${crumb.title}-${index}`}>
                {/* Разделитель — соседний <li>, а не вложенный: валидная вложенность списка */}
                {index > 0 ? <BreadcrumbSeparator /> : null}
                <BreadcrumbItem className="min-w-0">
                  {crumb.href ? (
                    <BreadcrumbLink asChild>
                      <Link to={crumb.href} className="truncate">
                        {crumb.title}
                      </Link>
                    </BreadcrumbLink>
                  ) : (
                    <BreadcrumbPage className="truncate">{crumb.title}</BreadcrumbPage>
                  )}
                </BreadcrumbItem>
              </Fragment>
            ))}
          </BreadcrumbList>
        </Breadcrumb>
      ) : null}

      <div className="ml-auto flex items-center gap-1.5">
        {env.mockApi ? (
          <span className="mr-1 hidden items-center gap-1.5 rounded-sm bg-status-warning-tint px-2 py-0.5 text-xs font-medium text-status-warning-deep lg:inline-flex">
            Режим фикстур
          </span>
        ) : null}
        {activeRole === 'candidate' && user?.consent.publish_at ? (
          <span
            title="Профиль виден работодателям"
            className="mr-1 hidden items-center gap-1.5 rounded-sm bg-status-success-tint px-2 py-0.5 text-xs font-medium text-status-success-deep lg:inline-flex"
          >
            <ShieldCheck className="size-3" aria-hidden="true" />
            Профиль опубликован
          </span>
        ) : null}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className="flex items-center gap-2 rounded-md px-1.5 py-1 outline-none transition-colors duration-150 hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
              aria-label="Меню профиля"
            >
              <Avatar className="size-8">
                <AvatarFallback className="bg-dark text-xs font-semibold text-dark-foreground">
                  {initialsOf(user?.email)}
                </AvatarFallback>
              </Avatar>
              <span className="hidden min-w-0 text-left leading-tight xl:block">
                <span className="block max-w-48 truncate text-[13px] font-semibold">
                  {user?.email ?? '…'}
                </span>
                {activeRole ? (
                  <span className="block text-xs text-muted-foreground">
                    {ROLE_LABELS[activeRole]}
                  </span>
                ) : null}
              </span>
              <ChevronDown className="hidden size-3.5 text-muted-foreground xl:block" aria-hidden="true" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-60">
            <DropdownMenuLabel className="truncate font-normal text-muted-foreground">
              {user?.email ?? '—'}
            </DropdownMenuLabel>
            {activeRole === 'candidate' ? (
              <DropdownMenuItem onSelect={() => navigate('/candidate/settings')}>
                <Settings aria-hidden="true" />
                Настройки и приватность
              </DropdownMenuItem>
            ) : null}
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => logout()}>
              <LogOut aria-hidden="true" />
              Выйти
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
