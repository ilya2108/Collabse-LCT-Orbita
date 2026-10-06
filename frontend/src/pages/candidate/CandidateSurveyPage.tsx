import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Illustration } from '@/shared/illustrations';
import { Button } from '@/shared/ui/button';
import { PageHeader } from '@/shared/ui/page-header';

/**
 * Шаг «Опрос и свершения» — честный cut сдачи 11.10: формы нет, шаг необязательный,
 * путь кандидата идёт дальше на тест.
 *
 * TODO(после сдачи): форма опроса (`POST /candidates/me/survey`) и свершения
 * (`GET|POST|DELETE /candidates/me/accomplishments`) — PLAN.md §3.6, §7.5; ответы задают
 * порядок узлов в сессии и контекст персональных сценарных заданий (scenario_personal).
 */
export function CandidateSurveyPage(): ReactNode {
  return (
    <>
      <PageHeader
        title="Опрос и свершения"
        subtitle="Необязательный шаг на пути до категории"
      />
      <section className="rounded-lg border border-border bg-card shadow-card">
        <div className="mx-auto flex max-w-[560px] flex-col items-center gap-3 px-6 py-12 text-center">
          <Illustration name="path-empty" height={140} />
          <p className="text-base font-semibold text-balance">
            Этот шаг можно пройти позже
          </p>
          <p className="text-sm leading-relaxed text-muted-foreground">
            Опрос и рассказ о свершениях — необязательный шаг: он помогает подобрать первые
            задания теста под ваш опыт, но на категорию и грейд не влияет. Категорию присваивает
            только тест.
          </p>
          <div className="mt-2 flex flex-wrap justify-center gap-2">
            <Button variant="outline" asChild>
              <Link to="/candidate">Пройти позже</Link>
            </Button>
            <Button variant="brand" asChild>
              <Link to="/candidate/test">К тесту</Link>
            </Button>
          </div>
        </div>
      </section>
    </>
  );
}
