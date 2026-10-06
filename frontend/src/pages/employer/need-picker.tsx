import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import { fetchNeeds } from '@/shared/api/endpoints/employers';
import type { NeedOut } from '@/shared/api/types';
import { GRADE_LABELS } from '@/shared/config/crm';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/ui/select';

/**
 * Выбор потребности для страниц «Подборка» и «Пайплайн»: ?need=<id> в URL
 * (переходы из «Потребностей» сохраняют контекст), по умолчанию — первая в списке.
 */

export interface NeedParam {
  needsQuery: UseQueryResult<{ items: NeedOut[]; total: number }>;
  needs: NeedOut[];
  need: NeedOut | null;
  setNeedId: (id: string) => void;
}

export function useNeedParam(): NeedParam {
  const [params, setParams] = useSearchParams();
  const needsQuery = useQuery({ queryKey: ['needs'], queryFn: ({ signal }) => fetchNeeds(signal) });
  const needs = needsQuery.data?.items ?? [];
  const requested = params.get('need');
  const need = needs.find((n) => n.id === requested) ?? needs[0] ?? null;
  const setNeedId = (id: string): void => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set('need', id);
      return next;
    });
  };
  return { needsQuery, needs, need, setNeedId };
}

export function NeedSelect({ needs, need, setNeedId }: NeedParam): ReactNode {
  return (
    <Select value={need?.id} onValueChange={setNeedId}>
      <SelectTrigger className="w-full max-w-[360px]" aria-label="Потребность">
        <SelectValue placeholder="Выберите потребность" />
      </SelectTrigger>
      <SelectContent>
        {needs.map((n) => (
          <SelectItem key={n.id} value={n.id}>
            {n.title} · {GRADE_LABELS[n.grade]}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
