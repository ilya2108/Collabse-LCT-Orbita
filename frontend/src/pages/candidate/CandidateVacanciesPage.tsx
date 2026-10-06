import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Send } from 'lucide-react';
import type { ReactNode } from 'react';
import {
  applyToVacancy,
  fetchVacancies,
  salaryRange,
  vacancyFormatLabel,
  type VacancyPublic,
} from '@/shared/api/endpoints/vacancies';
import { notifyApiError, notifySuccess } from '@/shared/api/feedback';
import { gradeLabel, specializationLabel } from '@/shared/config/labels';
import { Badge } from '@/shared/ui/badge';
import { Button } from '@/shared/ui/button';
import { PageHeader } from '@/shared/ui/page-header';
import { EmptyState, ErrorState, LoadingState } from '@/shared/ui/states';

/**
 * «Вакансии» кандидата: публичный список с вилкой «от–до» и откликом в один клик.
 * Отклик идемпотентен: повторный клик на бэкенде вернёт тот же отклик.
 */

function VacancyCard({ vacancy }: { vacancy: VacancyPublic }): ReactNode {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: () => applyToVacancy(vacancy.id),
    onSuccess: () => {
      notifySuccess(
        'Отклик отправлен',
        'Работодатель увидит вашу категорию и подтверждённые узлы графа',
      );
      void queryClient.invalidateQueries({ queryKey: ['vacancies', 'public'] });
    },
    onError: (error) => notifyApiError(error, 'Не удалось отправить отклик'),
  });

  return (
    <article className="flex flex-col gap-2 rounded-lg border border-border bg-card p-4 shadow-card">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-base font-semibold">{vacancy.title}</span>
        {vacancy.specialization_code || vacancy.grade ? (
          <Badge variant="secondary">
            {[specializationLabel(vacancy.specialization_code), gradeLabel(vacancy.grade)]
              .filter(Boolean)
              .join(' · ')}
          </Badge>
        ) : null}
        <span className="ml-auto text-sm font-semibold tabular-nums text-primary-deep">
          {salaryRange(vacancy.salary_from, vacancy.salary_to)}
        </span>
      </div>
      <p className="text-sm text-muted-foreground">{vacancy.company_name}</p>
      {vacancy.description ? (
        <p className="text-sm leading-relaxed text-muted-foreground">{vacancy.description}</p>
      ) : null}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-muted-foreground">
        {vacancy.format ? <span>{vacancyFormatLabel(vacancy.format)}</span> : null}
        {vacancy.need_nodes_count !== null ? (
          <span>Отклик оценят по {vacancy.need_nodes_count} узлам графа</span>
        ) : null}
        <span className="ml-auto">
          {vacancy.applied ? (
            <Badge variant="secondary">
              <Check /> Отклик отправлен
            </Badge>
          ) : (
            <Button
              size="sm"
              disabled={mutation.isPending}
              onClick={() => mutation.mutate()}
            >
              <Send /> {mutation.isPending ? 'Отправляем…' : 'Откликнуться'}
            </Button>
          )}
        </span>
      </div>
    </article>
  );
}

export function CandidateVacanciesPage(): ReactNode {
  const query = useQuery({
    queryKey: ['vacancies', 'public'],
    queryFn: ({ signal }) => fetchVacancies(signal),
  });

  return (
    <>
      <PageHeader title="Вакансии" subtitle="Открытые вакансии с вилкой и отклики" />

      <p className="mb-4 rounded-md bg-primary-tint p-2.5 text-xs leading-relaxed text-primary-deep">
        В «Орбите» работодатель обычно пишет первым — отклик здесь дополнительный канал.
      </p>

      {query.isPending ? (
        <LoadingState rows={6} />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : query.data.items.length === 0 ? (
        <section className="rounded-lg border border-border bg-card shadow-card">
          <EmptyState
            illustration="search-empty"
            title="Опубликованных вакансий пока нет"
            description="Они появятся, когда работодатели опубликуют вакансии. Работодатель видит отклик вместе с вашей категорией и подтверждёнными узлами, а не с резюме."
          />
        </section>
      ) : (
        <div className="flex flex-col gap-3">
          {query.data.items.map((vacancy) => (
            <VacancyCard key={vacancy.id} vacancy={vacancy} />
          ))}
        </div>
      )}
    </>
  );
}
