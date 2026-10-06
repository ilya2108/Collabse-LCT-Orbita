import { useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import {
  fetchMyVacancies,
  salaryRange,
  vacancyFormatLabel,
  type VacancyMine,
} from '@/shared/api/endpoints/vacancies';
import { gradeLabel, specializationLabel } from '@/shared/config/labels';
import { Badge } from '@/shared/ui/badge';
import { PageHeader } from '@/shared/ui/page-header';
import { EmptyState, ErrorState, LoadingState } from '@/shared/ui/states';

/**
 * «Вакансии» работодателя: публичные вакансии моей компании со счётчиком откликов.
 * Отклики приходят с категорией кандидата и покрытием подграфа, не с резюме.
 */

const VACANCY_STATUS_LABELS: Record<string, string> = {
  draft: 'Черновик',
  published: 'Опубликована',
  closed: 'Закрыта',
};

function applicationsLabel(count: number): string {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return `${count} отклик`;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${count} отклика`;
  return `${count} откликов`;
}

function VacancyCard({ vacancy }: { vacancy: VacancyMine }): ReactNode {
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
        {vacancy.status !== 'published' ? (
          <Badge variant="outline">{VACANCY_STATUS_LABELS[vacancy.status] ?? vacancy.status}</Badge>
        ) : null}
        <span className="ml-auto text-sm font-semibold tabular-nums text-primary-deep">
          {salaryRange(vacancy.salary_from, vacancy.salary_to)}
        </span>
      </div>
      {vacancy.description ? (
        <p className="text-sm leading-relaxed text-muted-foreground">{vacancy.description}</p>
      ) : null}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted-foreground">
        {vacancy.format ? <span>{vacancyFormatLabel(vacancy.format)}</span> : null}
        {vacancy.need_nodes_count !== null ? (
          <span>Связана с потребностью: {vacancy.need_nodes_count} узлов подграфа</span>
        ) : null}
        <span className="font-medium text-foreground">
          {applicationsLabel(vacancy.applications_count)}
        </span>
      </div>
    </article>
  );
}

export function EmployerVacanciesListPage(): ReactNode {
  const query = useQuery({
    queryKey: ['vacancies', 'mine'],
    queryFn: ({ signal }) => fetchMyVacancies(signal),
  });

  return (
    <>
      <PageHeader title="Вакансии" subtitle="Публичные вакансии и отклики кандидатов" />

      {query.isPending ? (
        <LoadingState rows={6} />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : query.data.items.length === 0 ? (
        <section className="rounded-lg border border-border bg-card shadow-card">
          <EmptyState
            illustration="box-empty"
            title="Вакансий пока нет"
            description="Вакансию можно связать с потребностью: отклики придут уже с категорией кандидата и покрытием нужного подграфа."
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
