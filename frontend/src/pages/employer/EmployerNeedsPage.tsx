import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Compass, KanbanSquare, Plus } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { createNeed, fetchNeeds } from '@/shared/api/endpoints/employers';
import { listSpecializations } from '@/shared/api/endpoints/skills';
import { notifyApiError, notifySuccess } from '@/shared/api/feedback';
import type { NeedGrade } from '@/shared/api/types';
import { GRADE_LABELS, NEED_STATUS_LABELS, NEED_STATUS_STATUS } from '@/shared/config/crm';
import { specializationLabel } from '@/shared/config/labels';
import { formatMoney, formatRelative } from '@/shared/lib/format';
import { Badge } from '@/shared/ui/badge';
import { Button } from '@/shared/ui/button';
import { StaggerGrid, StaggerItem } from '@/shared/ui/detail-blocks';
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
 * «Потребности»: список потребностей компании и минимальная форма создания
 * (POST /needs: заголовок, специализация, грейд, вилка «от–до», описание).
 * Из карточки — переходы в подборку и пайплайн по этой потребности.
 */

const GRADES: NeedGrade[] = ['junior', 'middle', 'senior'];

function CreateNeedDialog({ open, onClose }: { open: boolean; onClose: () => void }): ReactNode {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState('');
  const [specialization, setSpecialization] = useState('be');
  const [grade, setGrade] = useState<NeedGrade>('middle');
  const [salaryFrom, setSalaryFrom] = useState('');
  const [salaryTo, setSalaryTo] = useState('');
  const [rawText, setRawText] = useState('');

  const specsQuery = useQuery({
    queryKey: ['skills', 'specializations'],
    queryFn: ({ signal }) => listSpecializations(signal),
  });

  const mutation = useMutation({
    mutationFn: createNeed,
    onSuccess: (need) => {
      notifySuccess('Потребность создана', `«${need.title}» — подборка уже строится`);
      void queryClient.invalidateQueries({ queryKey: ['needs'] });
      onClose();
      setTitle('');
      setRawText('');
      setSalaryFrom('');
      setSalaryTo('');
    },
    onError: (error) => notifyApiError(error, 'Не удалось создать потребность'),
  });

  const from = Number(salaryFrom);
  const to = Number(salaryTo);
  const valid = title.trim() !== '' && from > 0 && to > 0 && from <= to;

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? undefined : onClose())}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Новая потребность</DialogTitle>
          <DialogDescription>
            Опишите, кого ищете, — платформа разберёт текст в подграф навыков и построит подборку.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor="need-title">Кого ищете</Label>
            <Input
              id="need-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Бэкенд-разработчик в платёжный сервис"
            />
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="grid gap-1.5">
              <Label>Специализация</Label>
              <Select value={specialization} onValueChange={setSpecialization}>
                <SelectTrigger aria-label="Специализация">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(specsQuery.data ?? []).map((spec) => (
                    <SelectItem key={spec.code} value={spec.code} disabled={spec.status !== 'active'}>
                      {spec.title}
                      {spec.status !== 'active' ? ' (скоро)' : ''}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1.5">
              <Label>Грейд</Label>
              <Select value={grade} onValueChange={(v) => setGrade(v as NeedGrade)}>
                <SelectTrigger aria-label="Грейд">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {GRADES.map((g) => (
                    <SelectItem key={g} value={g}>
                      {GRADE_LABELS[g]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="grid gap-1.5">
              <Label htmlFor="need-from">Зарплата от, ₽</Label>
              <Input
                id="need-from"
                type="number"
                inputMode="numeric"
                min={1}
                value={salaryFrom}
                onChange={(e) => setSalaryFrom(e.target.value)}
                placeholder="180000"
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="need-to">Зарплата до, ₽</Label>
              <Input
                id="need-to"
                type="number"
                inputMode="numeric"
                min={1}
                value={salaryTo}
                onChange={(e) => setSalaryTo(e.target.value)}
                placeholder="260000"
              />
            </div>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="need-text">Задачи и контекст (необязательно)</Label>
            <Textarea
              id="need-text"
              rows={4}
              value={rawText}
              onChange={(e) => setRawText(e.target.value)}
              placeholder="Что человек будет делать в первые три месяца, стек, команда…"
            />
          </div>
          <p className="text-xs text-muted-foreground">
            Вилка обязательна: кандидат видит её в приглашении. Текст пройдёт санитайзер перед
            разбором в подграф.
          </p>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Отмена
          </Button>
          <Button
            disabled={!valid || mutation.isPending}
            onClick={() =>
              mutation.mutate({
                title: title.trim(),
                specialization,
                grade,
                salary_from: from,
                salary_to: to,
                raw_text: rawText.trim() || undefined,
              })
            }
          >
            {mutation.isPending ? 'Создаём…' : 'Создать потребность'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function EmployerNeedsPage(): ReactNode {
  const [createOpen, setCreateOpen] = useState(false);
  const needsQuery = useQuery({ queryKey: ['needs'], queryFn: ({ signal }) => fetchNeeds(signal) });

  return (
    <>
      <PageHeader
        title="Потребности"
        subtitle="Текст потребности → подграф навыков → подборка и воронка найма"
        extra={
          <Button variant="brand" onClick={() => setCreateOpen(true)}>
            <Plus aria-hidden="true" />
            Новая потребность
          </Button>
        }
      />

      {needsQuery.isPending ? (
        <LoadingState rows={5} />
      ) : needsQuery.isError ? (
        <ErrorState error={needsQuery.error} onRetry={() => void needsQuery.refetch()} />
      ) : needsQuery.data.items.length === 0 ? (
        <section className="rounded-lg border border-border bg-card shadow-card">
          <EmptyState
            illustration="need-empty"
            title="Потребностей пока нет"
            description="Опишите, кого ищете и какие задачи ждут человека в первые три месяца, — подборка построится по категориям из теста, а не по резюме."
            actions={
              <Button variant="brand" onClick={() => setCreateOpen(true)}>
                Описать потребность
              </Button>
            }
          />
        </section>
      ) : (
        <StaggerGrid className="grid gap-4 md:grid-cols-2">
          {needsQuery.data.items.map((need) => (
            <StaggerItem key={need.id} className="h-full">
              <article className="flex h-full flex-col gap-3 rounded-lg border border-border bg-card p-5 shadow-card">
                <div className="flex items-start justify-between gap-3">
                  <h2 className="text-base font-semibold">{need.title}</h2>
                  <StatusBadge size="sm" status={NEED_STATUS_STATUS[need.status]}>
                    {NEED_STATUS_LABELS[need.status]}
                  </StatusBadge>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="secondary">{specializationLabel(need.specialization)}</Badge>
                  <Badge variant="secondary">{GRADE_LABELS[need.grade]}</Badge>
                  <Badge variant="outline">
                    {formatMoney(need.salary_from)} – {formatMoney(need.salary_to)}
                  </Badge>
                </div>
                {need.raw_text ? (
                  <p className="line-clamp-3 text-sm leading-relaxed text-muted-foreground">
                    {need.raw_text}
                  </p>
                ) : null}
                <div className="mt-auto flex flex-wrap items-center justify-between gap-2 pt-1">
                  <span className="text-xs text-muted-foreground">
                    Обновлена {formatRelative(need.updated_at)}
                  </span>
                  <span className="flex gap-2">
                    <Button variant="outline" size="sm" asChild>
                      <Link to={`/employer/selection?need=${need.id}`}>
                        <Compass aria-hidden="true" />
                        Подборка
                      </Link>
                    </Button>
                    <Button variant="outline" size="sm" asChild>
                      <Link to={`/employer/pipeline?need=${need.id}`}>
                        <KanbanSquare aria-hidden="true" />
                        Воронка найма
                      </Link>
                    </Button>
                  </span>
                </div>
              </article>
            </StaggerItem>
          ))}
        </StaggerGrid>
      )}

      <CreateNeedDialog open={createOpen} onClose={() => setCreateOpen(false)} />
    </>
  );
}
