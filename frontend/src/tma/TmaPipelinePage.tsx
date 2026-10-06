import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowRight, ChevronDown, ChevronLeft, XCircle } from 'lucide-react';
import { useCallback, type ReactNode } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
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
import { useDocumentTitle } from '@/shared/lib/useDocumentTitle';
import { Button } from '@/shared/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/shared/ui/collapsible';
import { IntegrityBadge } from '@/shared/ui/integrity-badge';
import { EmptyState, ErrorState, LoadingState } from '@/shared/ui/states';
import { StatusBadge } from '@/shared/ui/status-badge';
import { fetchTmaNeeds, fetchTmaPipeline, moveTmaPipelineStage } from './api';
import { useTmaBackButton } from './TmaLayout';

/**
 * Воронка найма потребности в мини-аппе: колонки-аккордеоны вместо доски (мобильный
 * формат), перемещение кнопками по тем же правилам переходов, что и в вебе
 * (словарь PIPELINE_MANUAL_TARGETS; interview/offer/hired — только после принятого
 * приглашения, иначе backend ответит 409). Назад — BackButton Telegram, если
 * доступен, плюс собственная кнопка для браузера.
 */

function PipelineCard({ needId, item }: { needId: string; item: PipelineItem }): ReactNode {
  const queryClient = useQueryClient();
  const moveMutation = useMutation({
    mutationFn: (stage: PipelineStage) => moveTmaPipelineStage(needId, item.candidate_id, stage),
    onSuccess: (pipeline: NeedPipelineOut) => {
      queryClient.setQueryData(['tma', 'needs', needId, 'pipeline'], pipeline);
    },
    onError: (error) => notifyApiError(error, 'Не удалось перевести стадию'),
    meta: { silent: true },
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
      {item.contacts_available && item.full_name_masked ? (
        <span className="text-xs text-muted-foreground">
          {item.full_name_masked} — контакты раскрыты, полные данные в веб-кабинете
        </span>
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
      {(forward || canDecline) && (
        <div className="flex gap-2 pt-1">
          {forward ? (
            <Button
              variant="secondary"
              className="flex-1"
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
              className="flex-1"
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
      <span className="text-[11px] text-muted-foreground">{formatRelative(item.updated_at)}</span>
    </article>
  );
}

export function TmaPipelinePage(): ReactNode {
  useDocumentTitle('Воронка найма');
  const { needId = '' } = useParams();
  const navigate = useNavigate();

  const goBack = useCallback(() => navigate('/tma'), [navigate]);
  useTmaBackButton(goBack);

  const needsQuery = useQuery({
    queryKey: ['tma', 'needs'],
    queryFn: () => fetchTmaNeeds(),
  });
  const need = needsQuery.data?.items.find((n) => n.id === needId) ?? null;

  const pipelineQuery = useQuery({
    queryKey: ['tma', 'needs', needId, 'pipeline'],
    queryFn: () => fetchTmaPipeline(needId),
    enabled: needId !== '',
  });

  return (
    <>
      <header className="flex flex-col gap-1">
        <Button
          variant="ghost"
          size="sm"
          className="-ml-2 w-fit text-muted-foreground"
          onClick={goBack}
        >
          <ChevronLeft aria-hidden="true" />К потребностям
        </Button>
        <h1 className="text-xl font-bold">Воронка найма</h1>
        {need ? <p className="text-sm text-muted-foreground">{need.title}</p> : null}
      </header>

      {pipelineQuery.isPending ? (
        <LoadingState rows={8} />
      ) : pipelineQuery.isError ? (
        <ErrorState error={pipelineQuery.error} onRetry={() => void pipelineQuery.refetch()} />
      ) : pipelineQuery.data.total === 0 ? (
        <section className="rounded-lg border border-border bg-card shadow-card">
          <EmptyState
            illustration="search-empty"
            title="На доске пока пусто"
            description="Добавьте кандидатов в шорт-лист из подборки в веб-кабинете — карточки появятся здесь."
          />
        </section>
      ) : (
        <div className="flex flex-col gap-2">
          {pipelineQuery.data.columns.map((column) => (
            <Collapsible
              key={column.stage}
              defaultOpen={column.items.length > 0}
              className="rounded-lg bg-muted/60"
            >
              <CollapsibleTrigger
                className="group flex min-h-12 w-full items-center justify-between gap-2 rounded-lg px-3 py-2 outline-none focus-visible:ring-2 focus-visible:ring-ring"
                aria-label={`${PIPELINE_STAGE_LABELS[column.stage]}: ${column.items.length}`}
              >
                <StatusBadge status={PIPELINE_STAGE_STATUS[column.stage]}>
                  {PIPELINE_STAGE_LABELS[column.stage]}
                </StatusBadge>
                <span className="flex items-center gap-1.5 text-sm tabular-nums text-muted-foreground">
                  {column.items.length}
                  <ChevronDown
                    aria-hidden="true"
                    className="size-4 transition-transform group-data-[state=open]:rotate-180"
                  />
                </span>
              </CollapsibleTrigger>
              <CollapsibleContent className="flex flex-col gap-2 px-2 pb-2">
                {column.items.length === 0 ? (
                  <p className="px-1 pb-1 text-xs text-muted-foreground">Пусто</p>
                ) : (
                  column.items.map((item) => (
                    <PipelineCard key={item.candidate_id} needId={needId} item={item} />
                  ))
                )}
              </CollapsibleContent>
            </Collapsible>
          ))}
        </div>
      )}
    </>
  );
}
