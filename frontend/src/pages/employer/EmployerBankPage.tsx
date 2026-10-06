import { useQuery } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { fetchCandidateBank, type BankCandidate } from '@/shared/api/endpoints/bank';
import { listSpecializations } from '@/shared/api/endpoints/skills';
import { gradeLabel, SPECIALIZATION_LABELS, specializationLabel } from '@/shared/config/labels';
import { formatDate } from '@/shared/lib/format';
import { Badge } from '@/shared/ui/badge';
import { FilterSelect } from '@/shared/ui/filter-select';
import { PageHeader } from '@/shared/ui/page-header';
import { EmptyState, ErrorState, LoadingState } from '@/shared/ui/states';

/**
 * «Банк кандидатов»: витрина всех кандидатов с категорией и опубликованным профилем
 * (GET /matching/bank). Кандидаты — только псевдонимами; контакты раскрываются после
 * принятого приглашения. Фильтры — специализация и грейд; сортирует backend
 * (senior → junior, затем Q-уровень).
 */

const GRADE_OPTIONS = ['junior', 'middle', 'senior'].map((code) => ({
  value: code,
  label: gradeLabel(code),
}));

const PAGE_LIMIT = 100;

function BankCard({ candidate }: { candidate: BankCandidate }): ReactNode {
  return (
    <article className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4 shadow-card">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-lg font-semibold tracking-tight">
          {candidate.pseudonym}
        </span>
        <Badge variant="secondary">
          {specializationLabel(candidate.specialization_code)} · {gradeLabel(candidate.grade)}
        </Badge>
        <Badge variant="outline">Q{candidate.q_level}</Badge>
        {candidate.shape ? <Badge variant="outline">{candidate.shape}-профиль</Badge> : null}
      </div>
      {candidate.stack_confirmed.length > 0 ? (
        <div className="flex flex-wrap items-center gap-1">
          <span className="text-xs text-muted-foreground">Подтверждённый стек:</span>
          {candidate.stack_confirmed.map((tag) => (
            <span
              key={tag}
              className="rounded-sm bg-status-success-tint px-1.5 py-px text-[11px] font-medium text-status-success-deep"
            >
              {tag}
            </span>
          ))}
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">Подтверждённого стека пока нет</p>
      )}
      <p className="mt-auto text-xs text-muted-foreground">
        Грейд подтверждён {formatDate(candidate.grade_confirmed_at)}
      </p>
    </article>
  );
}

export function EmployerBankPage(): ReactNode {
  const [specialization, setSpecialization] = useState<string | undefined>(undefined);
  const [grade, setGrade] = useState<string | undefined>(undefined);

  const specializationsQuery = useQuery({
    queryKey: ['skills', 'specializations'],
    queryFn: ({ signal }) => listSpecializations(signal),
  });
  const specializationOptions = (specializationsQuery.data ?? []).map((spec) => ({
    value: spec.code,
    label: spec.title || SPECIALIZATION_LABELS[spec.code] || spec.code,
  }));

  const bankQuery = useQuery({
    queryKey: ['matching', 'bank', specialization ?? null, grade ?? null],
    queryFn: ({ signal }) =>
      fetchCandidateBank({ specialization, grade, limit: PAGE_LIMIT }, signal),
  });

  return (
    <>
      <PageHeader
        title="Банк кандидатов"
        subtitle="Все кандидаты с категорией и опубликованным профилем"
        extra={
          <div className="flex flex-wrap items-center gap-2">
            <FilterSelect
              options={specializationOptions}
              value={specialization}
              onChange={setSpecialization}
              placeholder="Специализация"
            />
            <FilterSelect
              options={GRADE_OPTIONS}
              value={grade}
              onChange={setGrade}
              placeholder="Грейд"
            />
          </div>
        }
      />

      {bankQuery.isPending ? (
        <LoadingState rows={6} />
      ) : bankQuery.isError ? (
        <ErrorState error={bankQuery.error} onRetry={() => void bankQuery.refetch()} />
      ) : bankQuery.data.items.length === 0 ? (
        <section className="rounded-lg border border-border bg-card shadow-card">
          <EmptyState
            illustration="search-empty"
            title="Под фильтры никто не попал"
            description="Попробуйте снять фильтр грейда или специализации — банк показывает всех кандидатов с подтверждённой категорией."
          />
        </section>
      ) : (
        <div className="flex flex-col gap-3">
          <p className="text-sm text-muted-foreground">
            {bankQuery.data.total} кандидатов · под псевдонимами; контакты — после принятого
            приглашения.
          </p>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {bankQuery.data.items.map((candidate) => (
              <BankCard key={candidate.candidate_id} candidate={candidate} />
            ))}
          </div>
        </div>
      )}
    </>
  );
}
