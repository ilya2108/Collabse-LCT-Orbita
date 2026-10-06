import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowRight, Contact, XCircle } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { fetchNeedPipeline, movePipelineStage } from '@/shared/api/endpoints/employers';
import { fetchInvitationContacts } from '@/shared/api/endpoints/invitations';
import { notifyApiError } from '@/shared/api/feedback';
import type { NeedPipelineOut, PipelineItem, PipelineStage } from '@/shared/api/types';
import {
  INVITATION_STATUS_LABELS,
  INVITATION_STATUS_STATUS,
  PIPELINE_MANUAL_TARGETS,
  PIPELINE_STAGE_LABELS,
  PIPELINE_STAGE_STATUS,
} from '@/shared/config/crm';
import { formatRelative } from '@/shared/lib/format';
import { Button } from '@/shared/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/shared/ui/dialog';
import { DL, DLRow } from '@/shared/ui/detail-blocks';
import { IntegrityBadge } from '@/shared/ui/integrity-badge';
import { PageHeader } from '@/shared/ui/page-header';
import { EmptyState, ErrorState, LoadingState } from '@/shared/ui/states';
import { StatusBadge } from '@/shared/ui/status-badge';
import { NeedSelect, useNeedParam } from './need-picker';

/**
 * «Воронка найма» — мини-CRM доска потребности: колонки стадий (shortlist → … → hired),
 * перемещение кнопками (без dnd — доступнее и дешевле), история статуса приглашения
 * на карточке. Контакты кандидата появляются после принятого приглашения.
 */

function ContactsDialog({
  item,
  onClose,
}: {
  item: PipelineItem | null;
  onClose: () => void;
}): ReactNode {
  const contactsQuery = useQuery({
    queryKey: ['invitations', item?.invitation_id, 'contacts'],
    queryFn: ({ signal }) => fetchInvitationContacts(item?.invitation_id ?? '', signal),
    enabled: item !== null && item.invitation_id !== null,
  });
  return (
    <Dialog open={item !== null} onOpenChange={(next) => (next ? undefined : onClose())}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>Контакты {item?.pseudonym}</DialogTitle>
          <DialogDescription>
            Кандидат принял приглашение — контакты раскрыты обеим сторонам, доступ записан в
            аудит.
          </DialogDescription>
        </DialogHeader>
        {contactsQuery.isPending ? (
          <LoadingState rows={3} card={false} />
        ) : contactsQuery.isError ? (
          <ErrorState
            error={contactsQuery.error}
            onRetry={() => void contactsQuery.refetch()}
            compact
          />
        ) : (
          <DL labelWidth={100}>
            <DLRow label="ФИО">{contactsQuery.data.full_name ?? '—'}</DLRow>
            <DLRow label="Эл. почта">{contactsQuery.data.email ?? '—'}</DLRow>
            <DLRow label="Телефон">{contactsQuery.data.phone ?? '—'}</DLRow>
            <DLRow label="Telegram">{contactsQuery.data.telegram ?? '—'}</DLRow>
          </DL>
        )}
      </DialogContent>
    </Dialog>
  );
}

function PipelineCard({
  needId,
  item,
  onShowContacts,
}: {
  needId: string;
  item: PipelineItem;
  onShowContacts: (item: PipelineItem) => void;
}): ReactNode {
  const queryClient = useQueryClient();
  const moveMutation = useMutation({
    mutationFn: (stage: PipelineStage) => movePipelineStage(needId, item.candidate_id, { stage }),
    onSuccess: (pipeline: NeedPipelineOut) => {
      queryClient.setQueryData(['needs', needId, 'pipeline'], pipeline);
    },
    onError: (error) => notifyApiError(error, 'Не удалось перевести стадию'),
  });

  const targets = PIPELINE_MANUAL_TARGETS[item.stage];
  const forward = targets.find((t) => t !== 'declined');
  const canDecline = targets.includes('declined');

  return (
    <article className="flex flex-col gap-2 rounded-md border border-border bg-card p-3 shadow-card">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-semibold">{item.pseudonym}</span>
        {item.score !== null ? (
          <span className="text-xs tabular-nums text-primary-deep">
            {Math.round(item.score * 100)}%
          </span>
        ) : null}
      </div>
      {item.full_name_masked ? (
        <span className="text-xs text-muted-foreground">{item.full_name_masked}</span>
      ) : null}
      {item.invitation_status ? (
        <StatusBadge size="sm" status={INVITATION_STATUS_STATUS[item.invitation_status]}>
          приглашение: {INVITATION_STATUS_LABELS[item.invitation_status].toLowerCase()}
        </StatusBadge>
      ) : null}
      <IntegrityBadge level={item.integrity_level} />
      {item.note ? (
        <p className="text-xs leading-relaxed text-muted-foreground">{item.note}</p>
      ) : null}
      {item.contacts_available ? (
        <Button variant="outline" size="sm" onClick={() => onShowContacts(item)}>
          <Contact aria-hidden="true" />
          Контакты
        </Button>
      ) : null}
      {(forward || canDecline) && (
        <div className="flex flex-wrap gap-1.5">
          {forward ? (
            <Button
              variant="secondary"
              size="sm"
              disabled={moveMutation.isPending}
              onClick={() => moveMutation.mutate(forward)}
            >
              <ArrowRight aria-hidden="true" />
              {PIPELINE_STAGE_LABELS[forward]}
            </Button>
          ) : null}
          {canDecline ? (
            <Button
              variant="ghost"
              size="sm"
              disabled={moveMutation.isPending}
              onClick={() => moveMutation.mutate('declined')}
              aria-label={`Отказ: ${item.pseudonym}`}
            >
              <XCircle aria-hidden="true" />
              Отказ
            </Button>
          ) : null}
        </div>
      )}
      <span className="text-[11px] text-muted-foreground">
        {formatRelative(item.updated_at)}
      </span>
    </article>
  );
}

export function EmployerPipelinePage(): ReactNode {
  const needParam = useNeedParam();
  const { needsQuery, need } = needParam;
  const [contactsItem, setContactsItem] = useState<PipelineItem | null>(null);

  const pipelineQuery = useQuery({
    queryKey: ['needs', need?.id, 'pipeline'],
    queryFn: ({ signal }) => fetchNeedPipeline(need?.id ?? '', signal),
    enabled: need !== null,
  });

  return (
    <>
      <PageHeader
        title="Воронка найма"
        subtitle="Кандидаты по потребности: стадии от шорт-листа до найма; статусы приглашений обновляются автоматически"
        extra={<NeedSelect {...needParam} />}
      />

      {needsQuery.isPending ? (
        <LoadingState rows={6} />
      ) : needsQuery.isError ? (
        <ErrorState error={needsQuery.error} onRetry={() => void needsQuery.refetch()} />
      ) : need === null ? (
        <section className="rounded-lg border border-border bg-card shadow-card">
          <EmptyState
            illustration="need-empty"
            title="Сначала опишите потребность"
            description="Воронка ведётся по конкретной потребности: добавляйте кандидатов из подборки в шорт-лист и двигайте их по стадиям."
          />
        </section>
      ) : pipelineQuery.isPending ? (
        <LoadingState rows={8} />
      ) : pipelineQuery.isError ? (
        <ErrorState error={pipelineQuery.error} onRetry={() => void pipelineQuery.refetch()} />
      ) : pipelineQuery.data.total === 0 ? (
        <section className="rounded-lg border border-border bg-card shadow-card">
          <EmptyState
            illustration="search-empty"
            title="На доске пока пусто"
            description="Добавьте кандидатов из подборки в шорт-лист — карточки появятся здесь, а приглашения сами переведут их дальше."
            actions={
              <Button variant="brand" asChild>
                <Link to={`/employer/selection?need=${need.id}`}>Открыть подборку</Link>
              </Button>
            }
          />
        </section>
      ) : (
        <div className="overflow-x-auto pb-2">
          <div className="flex min-w-max gap-3">
            {pipelineQuery.data.columns.map((column) => (
              <section
                key={column.stage}
                className="flex w-[248px] shrink-0 flex-col gap-2 rounded-lg bg-muted/60 p-2"
                aria-label={PIPELINE_STAGE_LABELS[column.stage]}
              >
                <header className="flex items-center justify-between px-1 pt-1">
                  <StatusBadge size="sm" status={PIPELINE_STAGE_STATUS[column.stage]}>
                    {PIPELINE_STAGE_LABELS[column.stage]}
                  </StatusBadge>
                  <span className="text-xs tabular-nums text-muted-foreground">
                    {column.items.length}
                  </span>
                </header>
                {column.items.length === 0 ? (
                  <p className="px-1 pb-1 text-xs text-muted-foreground">Пусто</p>
                ) : (
                  column.items.map((item) => (
                    <PipelineCard
                      key={item.candidate_id}
                      needId={need.id}
                      item={item}
                      onShowContacts={setContactsItem}
                    />
                  ))
                )}
              </section>
            ))}
          </div>
        </div>
      )}

      <ContactsDialog item={contactsItem} onClose={() => setContactsItem(null)} />
    </>
  );
}
