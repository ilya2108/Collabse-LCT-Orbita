import { useQuery } from '@tanstack/react-query';
import { ChevronRight } from 'lucide-react';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import type { TmaMe } from '@/shared/api/types';
import { GRADE_LABELS, MEMBER_ROLE_LABELS } from '@/shared/config/crm';
import { formatMoney } from '@/shared/lib/format';
import { useDocumentTitle } from '@/shared/lib/useDocumentTitle';
import { StatusBadge } from '@/shared/ui/status-badge';
import { EmptyState, ErrorState, LoadingState } from '@/shared/ui/states';
import { fetchTmaNeeds } from './api';

/**
 * Экран работодателя/участника: активные потребности компании. Карточка целиком —
 * тач-таргет: тап ведёт на пайплайн потребности (/tma/needs/:id).
 */
export function TmaNeedsScreen({ me }: { me: TmaMe }): ReactNode {
  useDocumentTitle('Потребности');

  const query = useQuery({
    queryKey: ['tma', 'needs'],
    queryFn: () => fetchTmaNeeds(),
  });

  return (
    <>
      <header className="flex flex-col gap-1">
        <h1 className="text-xl font-bold">Потребности</h1>
        <p className="text-sm text-muted-foreground">
          {me.company_name ?? 'Компания'}
          {me.role ? ` · ${MEMBER_ROLE_LABELS[me.role]}` : ''} — выберите потребность, чтобы
          открыть воронку найма
        </p>
      </header>

      {query.isPending ? (
        <LoadingState rows={5} />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : query.data.items.length === 0 ? (
        <section className="rounded-lg border border-border bg-card shadow-card">
          <EmptyState
            illustration="need-empty"
            title="Активных потребностей нет"
            description="Опишите потребность в веб-кабинете — воронка найма по ней появится здесь."
          />
        </section>
      ) : (
        <nav aria-label="Активные потребности" className="flex flex-col gap-3">
          {query.data.items.map((need) => (
            <Link
              key={need.id}
              to={`/tma/needs/${need.id}`}
              className="flex items-center gap-3 rounded-lg border border-border bg-card p-4 shadow-card outline-none transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring active:bg-accent"
            >
              <span className="flex min-w-0 flex-1 flex-col gap-1.5">
                <span className="text-sm font-semibold leading-snug">{need.title}</span>
                <span className="flex flex-wrap items-center gap-2">
                  <StatusBadge size="sm" status="brand">
                    {GRADE_LABELS[need.grade]}
                  </StatusBadge>
                  <span className="text-xs tabular-nums text-primary-deep">
                    {formatMoney(need.salary_from)} – {formatMoney(need.salary_to)}
                  </span>
                </span>
              </span>
              <ChevronRight aria-hidden="true" className="size-5 shrink-0 text-muted-foreground" />
            </Link>
          ))}
        </nav>
      )}
    </>
  );
}
