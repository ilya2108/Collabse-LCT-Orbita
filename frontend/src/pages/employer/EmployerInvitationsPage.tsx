import { useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { fetchEmployerInvitations } from '@/shared/api/endpoints/invitations';
import {
  DECLINE_REASON_LABELS,
  INVITATION_STATUS_LABELS,
  INVITATION_STATUS_STATUS,
} from '@/shared/config/crm';
import { formatDate, formatMoney, formatRelative } from '@/shared/lib/format';
import { Button } from '@/shared/ui/button';
import { PageHeader } from '@/shared/ui/page-header';
import { EmptyState, ErrorState, LoadingState } from '@/shared/ui/states';
import { StatusBadge } from '@/shared/ui/status-badge';

/**
 * «Приглашения» работодателя: исходящие с вилкой «от–до» и статусами
 * отправлено → просмотрено → принято/отклонено (GET /employers/me/invitations).
 */
export function EmployerInvitationsPage(): ReactNode {
  const query = useQuery({
    queryKey: ['invitations', 'employer'],
    queryFn: ({ signal }) => fetchEmployerInvitations(signal),
  });

  return (
    <>
      <PageHeader
        title="Приглашения"
        subtitle="Отправлено · просмотрено · принято · отклонено; приглашение действует 14 дней"
      />

      {query.isPending ? (
        <LoadingState rows={6} />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : query.data.items.length === 0 ? (
        <section className="rounded-lg border border-border bg-card shadow-card">
          <EmptyState
            illustration="box-empty"
            title="Приглашений пока нет"
            description="Отправьте первое из подборки: кандидат увидит вилку «от–до» и объяснение «почему именно вы». Контакты раскроются после принятия."
            actions={
              <Button variant="brand" asChild>
                <Link to="/employer/selection">Открыть подборку</Link>
              </Button>
            }
          />
        </section>
      ) : (
        <div className="flex flex-col gap-3">
          {query.data.items.map((inv) => (
            <article
              key={inv.id}
              className="flex flex-col gap-2 rounded-lg border border-border bg-card p-4 shadow-card"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-base font-semibold">{inv.candidate_pseudonym}</span>
                <StatusBadge size="sm" status={INVITATION_STATUS_STATUS[inv.status]}>
                  {INVITATION_STATUS_LABELS[inv.status]}
                </StatusBadge>
                <span className="ml-auto text-sm font-medium tabular-nums">
                  {formatMoney(inv.salary_from)} – {formatMoney(inv.salary_to)}
                </span>
              </div>
              {inv.title ? <p className="text-sm font-medium">{inv.title}</p> : null}
              {inv.message ? (
                <p className="text-sm leading-relaxed text-muted-foreground">{inv.message}</p>
              ) : null}
              {inv.status === 'declined' && inv.decline_reason_code ? (
                <p className="text-sm text-status-danger-deep">
                  Причина отказа: {DECLINE_REASON_LABELS[inv.decline_reason_code].toLowerCase()}
                  {inv.decline_text ? ` — «${inv.decline_text}»` : ''}
                </p>
              ) : null}
              <p className="text-xs text-muted-foreground">
                Отправлено {formatRelative(inv.sent_at)}
                {inv.viewed_at ? ` · просмотрено ${formatRelative(inv.viewed_at)}` : ''}
                {inv.status === 'sent' || inv.status === 'viewed'
                  ? ` · действует до ${formatDate(inv.expires_at)}`
                  : ''}
              </p>
            </article>
          ))}
        </div>
      )}
    </>
  );
}
