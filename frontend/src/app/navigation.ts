import type { LucideIcon } from 'lucide-react';
import {
  Briefcase,
  Building2,
  CalendarCheck,
  ClipboardList,
  Compass,
  Database,
  FileText,
  Inbox,
  KanbanSquare,
  ListChecks,
  Mail,
  Medal,
  Route,
  Settings,
  Share2,
  UserRound,
  Users,
} from 'lucide-react';
import { matchPath } from 'react-router-dom';
import type { AppRole } from '@/shared/auth/roles';

/**
 * Единая точка правды по навигации обоих кабинетов: из этого конфига собираются
 * сайдбар, хлебные крошки и заголовки. Роль определяет лэйаут (PLAN.md §7.1):
 * кандидат — «Путь до категории» и кабинет; работодатель — потребность → подборка.
 */

export interface NavItem {
  key: string;
  path: string;
  label: string;
  icon: LucideIcon;
  /** Пункт доступен, но функция ещё не готова — показываем бейдж «скоро». */
  soon?: boolean;
}

export interface NavSection {
  key: string;
  /** Подпись секции в сайдбаре (не показывается у единственной секции). */
  label?: string;
  items: NavItem[];
}

export interface RoleNav {
  role: AppRole;
  /** Домашний маршрут роли (логотип и редиректы). */
  home: string;
  /** Подпись кабинета под логотипом. */
  title: string;
  sections: NavSection[];
}

export const CANDIDATE_NAV: RoleNav = {
  role: 'candidate',
  home: '/candidate',
  title: 'Кабинет кандидата',
  sections: [
    {
      key: 'main',
      items: [
        { key: 'path', path: '/candidate', label: 'Путь до категории', icon: Route },
        { key: 'profile', path: '/candidate/profile', label: 'Профиль', icon: UserRound },
        { key: 'test', path: '/candidate/test', label: 'Тест', icon: ListChecks },
        { key: 'category', path: '/candidate/category', label: 'Категория и грейд', icon: Medal },
        { key: 'invitations', path: '/candidate/invitations', label: 'Приглашения', icon: Mail },
        { key: 'vacancies', path: '/candidate/vacancies', label: 'Вакансии', icon: Briefcase },
        {
          key: 'weekly',
          path: '/candidate/weekly-task',
          label: 'Задание недели',
          icon: CalendarCheck,
          soon: true,
        },
      ],
    },
    {
      key: 'more',
      label: 'Ещё',
      items: [
        { key: 'graph', path: '/skills/graph/be', label: 'Граф навыков', icon: Share2 },
        { key: 'settings', path: '/candidate/settings', label: 'Настройки', icon: Settings },
      ],
    },
  ],
};

export const EMPLOYER_NAV: RoleNav = {
  role: 'employer',
  home: '/employer',
  title: 'Кабинет работодателя',
  sections: [
    {
      key: 'main',
      items: [
        { key: 'company', path: '/employer/company', label: 'Компания', icon: Building2 },
        { key: 'needs', path: '/employer/needs', label: 'Потребности', icon: ClipboardList },
        { key: 'selection', path: '/employer/selection', label: 'Подборка', icon: Compass },
        { key: 'pipeline', path: '/employer/pipeline', label: 'Воронка найма', icon: KanbanSquare },
        { key: 'bank', path: '/employer/bank', label: 'Банк кандидатов', icon: Database },
        { key: 'invitations', path: '/employer/invitations', label: 'Приглашения', icon: Inbox },
        { key: 'vacancies', path: '/employer/vacancies', label: 'Вакансии', icon: FileText },
      ],
    },
    {
      key: 'more',
      label: 'Ещё',
      items: [
        { key: 'graph', path: '/skills/graph/be', label: 'Граф навыков', icon: Share2 },
        { key: 'settings', path: '/employer/settings', label: 'Настройки', icon: Settings },
      ],
    },
  ],
};

/** Администратор без выбранной роли: только справочник (день 6). */
export const ADMIN_NAV: RoleNav = {
  role: 'admin',
  home: '/skills/graph/be',
  title: 'Администрирование',
  sections: [
    {
      key: 'main',
      items: [
        { key: 'graph', path: '/skills/graph/be', label: 'Граф навыков', icon: Share2 },
        { key: 'users', path: '/admin/users', label: 'Пользователи', icon: Users, soon: true },
      ],
    },
  ],
};

export function navForRole(role: AppRole | null): RoleNav {
  switch (role) {
    case 'employer':
      return EMPLOYER_NAV;
    case 'admin':
      return ADMIN_NAV;
    case 'candidate':
    default:
      return CANDIDATE_NAV;
  }
}

/** Ключ активного пункта меню для pathname: точное совпадение приоритетнее префикса. */
export function activeNavKey(nav: RoleNav, pathname: string): string | null {
  const items = nav.sections.flatMap((section) => section.items);
  const exact = items.find((item) => matchPath(item.path, pathname));
  if (exact) return exact.key;
  // Страница графа с другой специализацией — тот же пункт
  if (pathname.startsWith('/skills/graph/')) {
    return items.find((item) => item.key === 'graph')?.key ?? null;
  }
  const prefixed = items
    .filter((item) => item.path !== nav.home && matchPath({ path: `${item.path}/*` }, pathname))
    .sort((a, b) => b.path.length - a.path.length)[0];
  return prefixed?.key ?? null;
}

export interface Crumb {
  title: string;
  /** Путь для ссылки; последняя крошка ссылки не имеет. */
  href?: string;
}

/** Хлебные крошки: «Кабинет → Пункт меню»; для графа — «Граф навыков». */
export function breadcrumbsFor(nav: RoleNav, pathname: string): Crumb[] {
  if (pathname.startsWith('/skills/graph/')) {
    return [{ title: nav.title, href: nav.home }, { title: 'Граф навыков' }];
  }
  const key = activeNavKey(nav, pathname);
  const item = nav.sections.flatMap((s) => s.items).find((i) => i.key === key);
  if (!item || item.path === nav.home) return [{ title: nav.title }];
  return [{ title: nav.title, href: nav.home }, { title: item.label }];
}
