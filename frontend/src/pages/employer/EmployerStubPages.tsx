import type { ReactNode } from 'react';
import { StubPage } from '@/pages/StubPage';

/**
 * Разделы кабинета работодателя — пустые состояния (функции по дням PLAN.md §9).
 * «Компания», «Потребности», «Подборка», «Пайплайн» и «Приглашения» — настоящие
 * страницы в соседних файлах.
 */

export function EmployerBankPage(): ReactNode {
  return (
    <StubPage
      title="Банк кандидатов"
      subtitle="Все кандидаты с категорией и опубликованным профилем"
      illustration="search-empty"
      description={
        <>
          Поиск по специализации, грейду и подтверждённому стеку. Карточка показывает псевдоним,
          категорию и граф; контакты — только после принятого приглашения.
        </>
      }
    />
  );
}

export function EmployerVacanciesPage(): ReactNode {
  return (
    <StubPage
      title="Вакансии"
      subtitle="Публичные вакансии и отклики кандидатов"
      illustration="box-empty"
      description={
        <>
          Вакансию можно связать с потребностью: отклики придут уже с категорией кандидата и
          покрытием нужного подграфа.
        </>
      }
    />
  );
}
