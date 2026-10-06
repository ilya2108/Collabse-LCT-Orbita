import type { ReactNode } from 'react';
import { StubPage } from '@/pages/StubPage';

/**
 * Разделы кабинета кандидата — пустые состояния (функции по дням PLAN.md §9).
 * «Профиль», «Тест», «Категория», «Приглашения» и «Настройки» — настоящие страницы
 * в соседних файлах.
 */

export function CandidateVacanciesPage(): ReactNode {
  return (
    <StubPage
      title="Вакансии"
      subtitle="Открытые вакансии с вилкой и отклики"
      illustration="search-empty"
      description={
        <>
          Список публичных вакансий и ваши отклики. Работодатель видит отклик вместе с вашей
          категорией и подтверждёнными узлами, а не с резюме.
        </>
      }
    />
  );
}

export function CandidateWeeklyTaskPage(): ReactNode {
  return (
    <StubPage
      title="Задание недели"
      subtitle="Короткие регулярные задания обновляют подтверждения узлов"
      illustration="path-empty"
      description={
        <>
          Подтверждения со временем тускнеют; задание недели «реанимирует» узел без повторного
          теста. Назначается после получения категории.
        </>
      }
    />
  );
}
