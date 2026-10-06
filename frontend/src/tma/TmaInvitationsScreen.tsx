import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, X } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { notifyApiError, notifySuccess } from '@/shared/api/feedback';
import type {
  CompanyContacts,
  DeclineReason,
  InvitationOut,
  TmaMe,
} from '@/shared/api/types';
import {
  contactMethodLabel,
  DECLINE_REASON_LABELS,
  GRADE_LABELS,
  INVITATION_STATUS_LABELS,
  INVITATION_STATUS_STATUS,
} from '@/shared/config/crm';
import { formatDate, formatMoney, formatRelative } from '@/shared/lib/format';
import { useDocumentTitle } from '@/shared/lib/useDocumentTitle';
import { Button } from '@/shared/ui/button';
import { DL, DLRow } from '@/shared/ui/detail-blocks';
import { EmptyState, ErrorState, LoadingState } from '@/shared/ui/states';
import { StatusBadge } from '@/shared/ui/status-badge';
import { acceptTmaInvitation, declineTmaInvitation, fetchTmaInvitations } from './api';

/**
 * Экран кандидата «Приглашения»: вилка «от–до» и объяснение «почему вы»;
 * «Принять» раскрывает контакты компании прямо в карточке, «Отклонить»
 * разворачивает выбор причины (крупные кнопки — мобильный формат, без диалогов).
 */

const DECLINE_REASONS: DeclineReason[] = ['salary', 'not_relevant', 'busy', 'other'];

function explanationText(inv: InvitationOut): string | null {
  const text = inv.explanation['text'];
  return typeof text === 'string' && text !== '' ? text : null;
}

function ContactsBlock({ contacts }: { contacts: CompanyContacts }): ReactNode {
  return (
    <div className="rounded-md bg-status-success-tint p-3">
      <p className="mb-2 text-xs font-semibold text-status-success-deep">
        Контакты раскрыты обеим сторонам
      </p>
      <DL labelWidth={84}>
        <DLRow label="Связь">{contactMethodLabel(contacts.contact_method) || '—'}</DLRow>
        <DLRow label="Эл. почта">{contacts.contact_email ?? '—'}</DLRow>
        <DLRow label="Телефон">{contacts.contact_phone ?? '—'}</DLRow>
      </DL>
    </div>
  );
}

function InvitationCard({ invitation }: { invitation: InvitationOut }): ReactNode {
  const queryClient = useQueryClient();
  const [contacts, setContacts] = useState<CompanyContacts | null>(null);
  const [declining, setDeclining] = useState(false);

  const invalidate = (): void => {
    void queryClient.invalidateQueries({ queryKey: ['tma', 'invitations'] });
  };

  const acceptMutation = useMutation({
    mutationFn: () => acceptTmaInvitation(invitation.id),
    onSuccess: (result) => {
      setContacts(result.company_contacts);
      notifySuccess('Приглашение принято', 'Контакты компании раскрыты — напишите первым');
      invalidate();
    },
    onError: (error) => notifyApiError(error, 'Не удалось принять приглашение'),
    meta: { silent: true },
  });

  const declineMutation = useMutation({
    mutationFn: (reason: DeclineReason) =>
      declineTmaInvitation(invitation.id, { reason_code: reason }),
    onSuccess: () => {
      notifySuccess('Приглашение отклонено');
      setDeclining(false);
      invalidate();
    },
    onError: (error) => notifyApiError(error, 'Не удалось отклонить приглашение'),
    meta: { silent: true },
  });

  const open = invitation.status === 'sent' || invitation.status === 'viewed';
  const explanation = explanationText(invitation);

  return (
    <article className="flex flex-col gap-2.5 rounded-lg border border-border bg-card p-4 shadow-card">
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 flex-1 text-base font-semibold">{invitation.company_name}</span>
        <StatusBadge size="sm" status={INVITATION_STATUS_STATUS[invitation.status]}>
          {INVITATION_STATUS_LABELS[invitation.status]}
        </StatusBadge>
      </div>
      <p className="text-lg font-semibold tabular-nums text-primary-deep">
        {formatMoney(invitation.salary_from)} – {formatMoney(invitation.salary_to)}
      </p>
      {invitation.title ? <p className="text-sm font-medium">{invitation.title}</p> : null}
      {invitation.message ? (
        <p className="text-sm leading-relaxed text-muted-foreground">{invitation.message}</p>
      ) : null}
      {explanation ? (
        <p className="rounded-md bg-primary-tint p-2.5 text-xs leading-relaxed text-primary-deep">
          Почему вы: {explanation}
        </p>
      ) : null}

      {contacts ? <ContactsBlock contacts={contacts} /> : null}
      {!contacts && invitation.status === 'accepted' ? (
        <p className="text-sm text-status-success-deep">
          Вы приняли приглашение — контакты раскрыты
          {invitation.contact_method
            ? `, канал связи: ${contactMethodLabel(invitation.contact_method)}`
            : ''}
          .
        </p>
      ) : null}
      {invitation.status === 'declined' ? (
        <p className="text-sm text-muted-foreground">
          Отклонено
          {invitation.decline_reason_code
            ? `: ${DECLINE_REASON_LABELS[invitation.decline_reason_code].toLowerCase()}`
            : ''}
        </p>
      ) : null}

      {open && !declining ? (
        <div className="flex gap-2 pt-1">
          <Button
            variant="brand"
            size="lg"
            className="flex-1"
            disabled={acceptMutation.isPending}
            onClick={() => acceptMutation.mutate()}
          >
            <Check aria-hidden="true" />
            Принять
          </Button>
          <Button variant="outline" size="lg" className="flex-1" onClick={() => setDeclining(true)}>
            <X aria-hidden="true" />
            Отклонить
          </Button>
        </div>
      ) : null}

      {open && declining ? (
        <div className="flex flex-col gap-2 rounded-md bg-muted p-3">
          <p className="text-xs font-medium text-muted-foreground">
            Почему отклоняете? Причина помогает присылать более точные предложения.
          </p>
          {DECLINE_REASONS.map((code) => (
            <Button
              key={code}
              variant="outline"
              size="lg"
              disabled={declineMutation.isPending}
              onClick={() => declineMutation.mutate(code)}
            >
              {DECLINE_REASON_LABELS[code]}
            </Button>
          ))}
          <Button variant="ghost" size="lg" onClick={() => setDeclining(false)}>
            Отмена
          </Button>
        </div>
      ) : null}

      <span className="text-xs text-muted-foreground">
        Получено {formatRelative(invitation.sent_at)}
        {open ? ` · действует до ${formatDate(invitation.expires_at)}` : ''}
      </span>
    </article>
  );
}

export function TmaInvitationsScreen({ me }: { me: TmaMe }): ReactNode {
  useDocumentTitle('Приглашения');

  const query = useQuery({
    queryKey: ['tma', 'invitations'],
    queryFn: () => fetchTmaInvitations(),
  });

  return (
    <>
      <header className="flex flex-col gap-1">
        <h1 className="text-xl font-bold">Приглашения</h1>
        <p className="text-sm text-muted-foreground">
          {me.pseudonym ?? 'Кандидат'}
          {me.grade ? ` · ${GRADE_LABELS[me.grade]}` : ''} — работодатель приходит первым, с
          вилкой «от–до»
        </p>
      </header>

      {query.isPending ? (
        <LoadingState rows={6} />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : query.data.items.length === 0 ? (
        <section className="rounded-lg border border-border bg-card shadow-card">
          <EmptyState
            illustration="box-empty"
            title="Приглашений пока нет"
            description="Они появятся, когда работодатели найдут вас по подтверждённым узлам графа — бот пришлёт уведомление."
          />
        </section>
      ) : (
        <div className="flex flex-col gap-3">
          {query.data.items.map((inv) => (
            <InvitationCard key={inv.id} invitation={inv} />
          ))}
        </div>
      )}
    </>
  );
}
