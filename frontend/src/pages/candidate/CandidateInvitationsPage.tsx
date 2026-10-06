import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, X } from 'lucide-react';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import {
  acceptInvitation,
  declineInvitation,
  fetchCandidateInvitations,
  markInvitationViewed,
} from '@/shared/api/endpoints/invitations';
import { notifyApiError, notifySuccess } from '@/shared/api/feedback';
import type { CompanyContacts, DeclineReason, InvitationOut } from '@/shared/api/types';
import {
  contactMethodLabel,
  DECLINE_REASON_LABELS,
  INVITATION_STATUS_LABELS,
  INVITATION_STATUS_STATUS,
} from '@/shared/config/crm';
import { formatDate, formatMoney, formatRelative } from '@/shared/lib/format';
import { Button } from '@/shared/ui/button';
import { DL, DLRow } from '@/shared/ui/detail-blocks';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/ui/dialog';
import { Label } from '@/shared/ui/label';
import { PageHeader } from '@/shared/ui/page-header';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/ui/select';
import { EmptyState, ErrorState, LoadingState } from '@/shared/ui/states';
import { StatusBadge } from '@/shared/ui/status-badge';
import { Textarea } from '@/shared/ui/textarea';

/**
 * «Приглашения» кандидата: работодатель приходит первым — с вилкой «от–до» и
 * объяснением «почему вы». Принятие раскрывает контакты компании (contact_grant);
 * отметка «просмотрено» ставится явным POST /view при открытии списка.
 */

const DECLINE_REASONS: DeclineReason[] = ['salary', 'not_relevant', 'busy', 'other'];

function explanationText(inv: InvitationOut): string | null {
  const text = inv.explanation['text'];
  return typeof text === 'string' && text !== '' ? text : null;
}

function ContactsBlock({ contacts }: { contacts: CompanyContacts }): ReactNode {
  return (
    <DL labelWidth={110}>
      <DLRow label="Компания">{contacts.company_name}</DLRow>
      <DLRow label="Связь">{contactMethodLabel(contacts.contact_method) || '—'}</DLRow>
      <DLRow label="Эл. почта">{contacts.contact_email ?? '—'}</DLRow>
      <DLRow label="Телефон">{contacts.contact_phone ?? '—'}</DLRow>
    </DL>
  );
}

function DeclineDialog({
  invitation,
  onClose,
}: {
  invitation: InvitationOut | null;
  onClose: () => void;
}): ReactNode {
  const queryClient = useQueryClient();
  const [reason, setReason] = useState<DeclineReason>('salary');
  const [text, setText] = useState('');

  const mutation = useMutation({
    mutationFn: (id: string) =>
      declineInvitation(id, { reason_code: reason, text: text.trim() || undefined }),
    onSuccess: () => {
      notifySuccess('Приглашение отклонено', 'Работодатель увидит причину без подробностей о вас');
      void queryClient.invalidateQueries({ queryKey: ['invitations', 'candidate'] });
      onClose();
      setText('');
    },
    onError: (error) => notifyApiError(error, 'Не удалось отклонить приглашение'),
  });

  return (
    <Dialog open={invitation !== null} onOpenChange={(next) => (next ? undefined : onClose())}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Отклонить приглашение</DialogTitle>
          <DialogDescription>
            Причина поможет работодателям присылать более точные предложения.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="grid gap-1.5">
            <Label>Причина</Label>
            <Select value={reason} onValueChange={(v) => setReason(v as DeclineReason)}>
              <SelectTrigger aria-label="Причина отказа">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {DECLINE_REASONS.map((code) => (
                  <SelectItem key={code} value={code}>
                    {DECLINE_REASON_LABELS[code]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="decline-text">Комментарий (необязательно)</Label>
            <Textarea
              id="decline-text"
              rows={2}
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Отмена
          </Button>
          <Button
            variant="destructive"
            disabled={mutation.isPending}
            onClick={() => invitation && mutation.mutate(invitation.id)}
          >
            {mutation.isPending ? 'Отправляем…' : 'Отклонить'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function CandidateInvitationsPage(): ReactNode {
  const queryClient = useQueryClient();
  const [declineTarget, setDeclineTarget] = useState<InvitationOut | null>(null);
  const [acceptedContacts, setAcceptedContacts] = useState<CompanyContacts | null>(null);
  const viewedRef = useRef(new Set<string>());

  const query = useQuery({
    queryKey: ['invitations', 'candidate'],
    queryFn: ({ signal }) => fetchCandidateInvitations(signal),
  });

  // Явная отметка «просмотрено»: страница открыта — отправляем view по каждому sent
  const sentIds = (query.data?.items ?? [])
    .filter((inv) => inv.status === 'sent')
    .map((inv) => inv.id)
    .join(',');
  useEffect(() => {
    if (!sentIds) return;
    for (const id of sentIds.split(',')) {
      if (viewedRef.current.has(id)) continue;
      viewedRef.current.add(id);
      markInvitationViewed(id)
        .then(() => queryClient.invalidateQueries({ queryKey: ['invitations', 'candidate'] }))
        .catch(() => undefined);
    }
  }, [sentIds, queryClient]);

  const acceptMutation = useMutation({
    mutationFn: acceptInvitation,
    onSuccess: (result) => {
      setAcceptedContacts(result.company_contacts);
      notifySuccess('Приглашение принято', 'Контакты раскрыты обеим сторонам');
      void queryClient.invalidateQueries({ queryKey: ['invitations', 'candidate'] });
    },
    onError: (error) => notifyApiError(error, 'Не удалось принять приглашение'),
  });

  return (
    <>
      <PageHeader
        title="Приглашения"
        subtitle="Работодатель приходит первым — с вилкой «от–до» и объяснением «почему вы»"
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
            description="Они появятся, когда у вас будет категория и опубликованный профиль: работодатели ищут по подтверждённым узлам графа, не по резюме."
          />
        </section>
      ) : (
        <div className="flex flex-col gap-3">
          {query.data.items.map((inv) => {
            const open = inv.status === 'sent' || inv.status === 'viewed';
            const explanation = explanationText(inv);
            return (
              <article
                key={inv.id}
                className="flex flex-col gap-2 rounded-lg border border-border bg-card p-4 shadow-card"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-base font-semibold">{inv.company_name}</span>
                  <StatusBadge size="sm" status={INVITATION_STATUS_STATUS[inv.status]}>
                    {INVITATION_STATUS_LABELS[inv.status]}
                  </StatusBadge>
                  <span className="ml-auto text-sm font-semibold tabular-nums text-primary-deep">
                    {formatMoney(inv.salary_from)} – {formatMoney(inv.salary_to)}
                  </span>
                </div>
                {inv.title ? <p className="text-sm font-medium">{inv.title}</p> : null}
                {inv.message ? (
                  <p className="text-sm leading-relaxed text-muted-foreground">{inv.message}</p>
                ) : null}
                {explanation ? (
                  <p className="rounded-md bg-primary-tint p-2.5 text-xs leading-relaxed text-primary-deep">
                    Почему вы: {explanation}
                  </p>
                ) : null}
                {inv.status === 'accepted' ? (
                  <p className="text-sm text-status-success-deep">
                    Вы приняли приглашение — контакты раскрыты
                    {inv.contact_method ? `, канал связи: ${contactMethodLabel(inv.contact_method)}` : ''}.
                  </p>
                ) : null}
                {inv.status === 'declined' ? (
                  <p className="text-sm text-muted-foreground">
                    Отклонено
                    {inv.decline_reason_code
                      ? `: ${DECLINE_REASON_LABELS[inv.decline_reason_code].toLowerCase()}`
                      : ''}
                  </p>
                ) : null}
                <div className="flex flex-wrap items-center gap-2 pt-1">
                  {open ? (
                    <>
                      <Button
                        variant="brand"
                        size="sm"
                        disabled={acceptMutation.isPending}
                        onClick={() => acceptMutation.mutate(inv.id)}
                      >
                        <Check aria-hidden="true" />
                        Принять
                      </Button>
                      <Button variant="outline" size="sm" onClick={() => setDeclineTarget(inv)}>
                        <X aria-hidden="true" />
                        Отклонить
                      </Button>
                    </>
                  ) : null}
                  <span className="ml-auto text-xs text-muted-foreground">
                    Получено {formatRelative(inv.sent_at)}
                    {open ? ` · действует до ${formatDate(inv.expires_at)}` : ''}
                  </span>
                </div>
              </article>
            );
          })}
        </div>
      )}

      <DeclineDialog invitation={declineTarget} onClose={() => setDeclineTarget(null)} />

      <Dialog
        open={acceptedContacts !== null}
        onOpenChange={(next) => (next ? undefined : setAcceptedContacts(null))}
      >
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Контакты компании</DialogTitle>
            <DialogDescription>
              Работодатель получил и ваши контакты — ждите сообщения или напишите первым.
            </DialogDescription>
          </DialogHeader>
          {acceptedContacts ? <ContactsBlock contacts={acceptedContacts} /> : null}
          <DialogFooter>
            <Button onClick={() => setAcceptedContacts(null)}>Понятно</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
