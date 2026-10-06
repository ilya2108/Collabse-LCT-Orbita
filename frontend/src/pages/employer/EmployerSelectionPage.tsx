import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ListPlus, Loader2, Mail, ShieldAlert, ShieldCheck, Sparkles } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import {
  addToPipeline,
  fetchNeedCandidates,
  fetchSelectionSummary,
} from '@/shared/api/endpoints/employers';
import { createInvitation } from '@/shared/api/endpoints/invitations';
import { notifyApiError, notifySuccess } from '@/shared/api/feedback';
import { isApiError } from '@/shared/api/errors';
import type { NeedOut, RankedCandidate } from '@/shared/api/types';
import { GRADE_LABELS } from '@/shared/config/crm';
import { formatDateTime } from '@/shared/lib/format';
import { toastInfo } from '@/shared/lib/toast';
import { Badge } from '@/shared/ui/badge';
import { Button } from '@/shared/ui/button';
import { SectionCard } from '@/shared/ui/detail-blocks';
import { IntegrityBadge } from '@/shared/ui/integrity-badge';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/ui/dialog';
import { Input } from '@/shared/ui/input';
import { Label } from '@/shared/ui/label';
import { PageHeader } from '@/shared/ui/page-header';
import { EmptyState, ErrorState, LoadingState } from '@/shared/ui/states';
import { StatusBadge } from '@/shared/ui/status-badge';
import { Textarea } from '@/shared/ui/textarea';
import { NeedSelect, useNeedParam } from './need-picker';

/**
 * «Подборка»: ранжированные кандидаты под потребность (GET /needs/{id}/candidates)
 * с объяснением «почему здесь» — покрытые/частичные/недостающие узлы подграфа.
 * Неподтверждённый грейд помечается бейджем и стоит ниже в списке (так ранжирует backend).
 */

function nodeCode(nodeId: string): string {
  return nodeId.split('.')[1] ?? nodeId;
}

function NodeChips({
  title,
  nodes,
  className,
}: {
  title: string;
  nodes: string[];
  className: string;
}): ReactNode {
  if (nodes.length === 0) return null;
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <span className="text-xs text-muted-foreground">{title}:</span>
      {nodes.map((node) => (
        <span
          key={node}
          className={`rounded-sm px-1.5 py-px text-[11px] font-medium ${className}`}
        >
          {nodeCode(node)}
        </span>
      ))}
    </span>
  );
}

function InviteDialog({
  need,
  candidate,
  onClose,
}: {
  need: NeedOut;
  candidate: RankedCandidate | null;
  onClose: () => void;
}): ReactNode {
  const queryClient = useQueryClient();
  const [message, setMessage] = useState('');
  const [salaryFrom, setSalaryFrom] = useState(String(need.salary_from));
  const [salaryTo, setSalaryTo] = useState(String(need.salary_to));

  const mutation = useMutation({
    mutationFn: createInvitation,
    onSuccess: (result) => {
      notifySuccess(
        'Приглашение отправлено',
        `Кандидат ${result.invitation.candidate_pseudonym ?? ''} увидит вилку и объяснение «почему вы»`,
      );
      if (result.below_salary_floor) {
        toastInfo(
          'Вилка ниже планки кандидата',
          'Кандидат указал зарплатную планку выше вашей вилки — вероятность отказа выше.',
        );
      }
      void queryClient.invalidateQueries({ queryKey: ['needs', need.id, 'pipeline'] });
      void queryClient.invalidateQueries({ queryKey: ['invitations', 'employer'] });
      onClose();
      setMessage('');
    },
    onError: (error) => notifyApiError(error, 'Не удалось отправить приглашение'),
  });

  const from = Number(salaryFrom);
  const to = Number(salaryTo);
  const valid = from > 0 && to > 0 && from <= to;

  return (
    <Dialog open={candidate !== null} onOpenChange={(next) => (next ? undefined : onClose())}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Пригласить {candidate?.pseudonym}</DialogTitle>
          <DialogDescription>
            Вы приходите первым: приглашение уходит с вилкой «от–до» и объяснением по графу.
            Контакты раскроются только после принятия.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="grid gap-1.5">
              <Label htmlFor="inv-from">Зарплата от, ₽</Label>
              <Input
                id="inv-from"
                type="number"
                inputMode="numeric"
                value={salaryFrom}
                onChange={(e) => setSalaryFrom(e.target.value)}
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="inv-to">Зарплата до, ₽</Label>
              <Input
                id="inv-to"
                type="number"
                inputMode="numeric"
                value={salaryTo}
                onChange={(e) => setSalaryTo(e.target.value)}
              />
            </div>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="inv-message">Сообщение кандидату (необязательно)</Label>
            <Textarea
              id="inv-message"
              rows={3}
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              placeholder="Почему зовёте и что ждёт в первые месяцы"
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Отмена
          </Button>
          <Button
            variant="brand"
            disabled={!valid || mutation.isPending}
            onClick={() => {
              if (!candidate) return;
              mutation.mutate({
                candidate_id: candidate.candidate_id,
                need_id: need.id,
                title: need.title,
                message: message.trim() || undefined,
                salary_from: from,
                salary_to: to,
              });
            }}
          >
            <Mail aria-hidden="true" />
            {mutation.isPending ? 'Отправляем…' : 'Отправить приглашение'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Картина по кандидатам (GET /employers/needs/{id}/selection-summary)
// ---------------------------------------------------------------------------

/** «Картина по кандидатам»: оверфит, недобор и возможные компромиссы по выдаче —
 * глазами ИИ. Запрос уходит ТОЛЬКО по клику (бережём дневной лимит GigaChat), дальше
 * ответ кэшируется бэком; генерация LLM может идти до пары минут. 404 — подборки ещё
 * нет; `source === 'rules'` — упрощённый разбор без ИИ (бейдж-пометка). */
function SelectionAiSummaryCard({ needId }: { needId: string }): ReactNode {
  const summaryQuery = useQuery({
    queryKey: ['needs', needId, 'selection-summary'],
    queryFn: ({ signal }) => fetchSelectionSummary(needId, signal),
    enabled: false,
    retry: false,
    staleTime: Infinity,
  });

  const summary = summaryQuery.data ?? null;
  // 404 — штатно: подборки по потребности ещё нет
  const noData =
    summaryQuery.isError &&
    isApiError(summaryQuery.error) &&
    summaryQuery.error.status === 404;

  return (
    <SectionCard
      title="Картина по кандидатам"
      extra={
        summary?.source === 'rules' ? (
          <StatusBadge size="sm">упрощённый разбор без ИИ</StatusBadge>
        ) : undefined
      }
    >
      {summaryQuery.isFetching ? (
        <div className="flex items-center gap-3 py-1">
          <Loader2 className="size-5 shrink-0 animate-spin text-primary" aria-hidden="true" />
          <p className="text-sm leading-relaxed text-muted-foreground">
            ИИ-консультант оценивает рынок по потребности — до пары минут.
          </p>
        </div>
      ) : summary ? (
        <>
          <div className="grid gap-2">
            {summary.summary.split('\n\n').map((paragraph, index) => (
              // Абзацы саммари статичны после загрузки — индекс безопасен
              // eslint-disable-next-line react/no-array-index-key
              <p key={index} className="text-sm leading-relaxed">
                {paragraph}
              </p>
            ))}
          </div>
          <p className="mt-3 text-xs text-muted-foreground">
            Сгенерировано {formatDateTime(summary.generated_at)}
          </p>
        </>
      ) : noData ? (
        <p className="text-sm leading-relaxed text-muted-foreground">
          Подборки по этой потребности пока нет — оценка появится, когда найдутся кандидаты.
        </p>
      ) : summaryQuery.isError ? (
        <>
          <p className="text-sm leading-relaxed text-muted-foreground">
            Не получилось подготовить оценку. Попробуйте ещё раз — обычно помогает.
          </p>
          <Button
            variant="outline"
            size="sm"
            className="mt-3"
            onClick={() => void summaryQuery.refetch()}
          >
            Попробовать ещё раз
          </Button>
        </>
      ) : (
        <>
          <p className="text-sm leading-relaxed text-muted-foreground">
            Оверфит, недобор, компромиссы — глазами ИИ: честная оценка, есть ли на рынке
            кандидаты под эту потребность и на какие уступки стоит пойти.
          </p>
          <Button
            variant="brand"
            size="sm"
            className="mt-3"
            onClick={() => void summaryQuery.refetch()}
          >
            <Sparkles aria-hidden="true" />
            Оценить рынок по этой потребности
          </Button>
        </>
      )}
    </SectionCard>
  );
}

export function EmployerSelectionPage(): ReactNode {
  const needParam = useNeedParam();
  const { needsQuery, need } = needParam;
  const queryClient = useQueryClient();
  const [inviteTarget, setInviteTarget] = useState<RankedCandidate | null>(null);

  const candidatesQuery = useQuery({
    queryKey: ['needs', need?.id, 'candidates'],
    queryFn: ({ signal }) => fetchNeedCandidates(need?.id ?? '', signal),
    enabled: need !== null,
  });

  const shortlistMutation = useMutation({
    mutationFn: (candidateId: string) =>
      addToPipeline(need?.id ?? '', { candidate_id: candidateId }),
    onSuccess: () => {
      notifySuccess('Кандидат в шорт-листе', 'Карточка появилась в воронке найма');
      void queryClient.invalidateQueries({ queryKey: ['needs', need?.id, 'pipeline'] });
    },
    onError: (error) => {
      if (isApiError(error) && error.code === 'pipeline_entry_exists') {
        toastInfo('Кандидат уже в воронке этой потребности');
        return;
      }
      notifyApiError(error, 'Не удалось добавить в шорт-лист');
    },
  });

  return (
    <>
      <PageHeader
        title="Подборка"
        subtitle="Рейтинг по покрытию подграфа потребности — с объяснением «почему здесь»"
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
            description="Подборка строится под конкретную потребность: текст разбирается в подграф навыков, кандидаты ранжируются по его покрытию."
          />
        </section>
      ) : candidatesQuery.isPending ? (
        <LoadingState rows={8} />
      ) : candidatesQuery.isError ? (
        <ErrorState
          error={candidatesQuery.error}
          onRetry={() => void candidatesQuery.refetch()}
        />
      ) : (
        <div className="flex flex-col gap-3">
          <SelectionAiSummaryCard needId={need.id} />
          <p className="text-sm text-muted-foreground">
            {candidatesQuery.data.total} кандидатов · основание ранжирования —{' '}
            {candidatesQuery.data.basis === 'subgraph'
              ? 'подграф потребности'
              : candidatesQuery.data.basis === 'grade_profile'
                ? 'профиль грейда'
                : 'сила профиля'}
            . Кандидаты под псевдонимами; контакты — после принятого приглашения.
          </p>
          {candidatesQuery.data.items.map((cand, index) => (
            <article
              key={cand.candidate_id}
              className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4 shadow-card"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="w-7 text-sm font-semibold tabular-nums text-muted-foreground">
                  {index + 1}
                </span>
                <span className="text-base font-semibold">{cand.pseudonym}</span>
                <Badge variant="secondary">{GRADE_LABELS[cand.grade]}</Badge>
                {cand.grade_confirmed ? (
                  <StatusBadge size="sm" status="success" icon={ShieldCheck}>
                    грейд подтверждён
                  </StatusBadge>
                ) : (
                  <StatusBadge size="sm" status="warning" icon={ShieldAlert}>
                    грейд не подтверждён
                  </StatusBadge>
                )}
                {cand.q_level !== null ? (
                  <Badge variant="outline">Q{cand.q_level}</Badge>
                ) : null}
                {cand.shape ? <Badge variant="outline">{cand.shape}-профиль</Badge> : null}
                <IntegrityBadge level={cand.integrity_level} />
                <span className="ml-auto flex items-baseline gap-3 text-sm">
                  <span>
                    совпадение{' '}
                    <b className="text-base tabular-nums text-primary-deep">
                      {Math.round(cand.score * 100)}%
                    </b>
                  </span>
                  <span className="text-muted-foreground">
                    покрытие {Math.round(cand.coverage * 100)}%
                  </span>
                </span>
              </div>
              <p className="text-sm leading-relaxed text-muted-foreground">
                {cand.explanation.text}
              </p>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
                <NodeChips
                  title="Покрыто"
                  nodes={cand.explanation.matched_nodes}
                  className="bg-status-success-tint text-status-success-deep"
                />
                <NodeChips
                  title="Частично"
                  nodes={cand.explanation.partial_nodes}
                  className="bg-status-warning-tint text-status-warning-deep"
                />
                <NodeChips
                  title="Не хватает"
                  nodes={cand.explanation.missing_nodes}
                  className="bg-muted text-muted-foreground"
                />
              </div>
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={shortlistMutation.isPending}
                  onClick={() => shortlistMutation.mutate(cand.candidate_id)}
                >
                  <ListPlus aria-hidden="true" />В шорт-лист
                </Button>
                <Button variant="brand" size="sm" onClick={() => setInviteTarget(cand)}>
                  <Mail aria-hidden="true" />
                  Пригласить
                </Button>
              </div>
            </article>
          ))}
        </div>
      )}

      {need ? (
        <InviteDialog need={need} candidate={inviteTarget} onClose={() => setInviteTarget(null)} />
      ) : null}
    </>
  );
}
