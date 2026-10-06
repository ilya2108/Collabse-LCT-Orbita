import { ArrowRight, Compass, Database, Mail, Share2, type LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Illustration } from '@/shared/illustrations';
import { Button } from '@/shared/ui/button';
import { StaggerGrid, StaggerItem } from '@/shared/ui/detail-blocks';
import { PageHeader } from '@/shared/ui/page-header';

/**
 * Главная работодателя — пустое состояние «Опишите потребность»: один большой CTA
 * и три шага сценария (потребность → подборка → приглашение). Форма потребности
 * появится на следующих днях (PLAN.md §9), сейчас — заглушка раздела.
 */

const STEPS: { icon: LucideIcon; title: string; text: string; to: string }[] = [
  {
    icon: Compass,
    title: 'Подборка по категориям',
    text: 'Платформа разберёт потребность в подграф навыков и покажет рекомендованные категории.',
    to: '/employer/selection',
  },
  {
    icon: Database,
    title: 'Банк кандидатов',
    text: 'Кандидаты с подтверждённой тестом категорией — под псевдонимами, без резюме.',
    to: '/employer/bank',
  },
  {
    icon: Mail,
    title: 'Приглашение с зарплатой',
    text: 'Вы приходите первым: вилка «от–до» и объяснение «почему именно вы».',
    to: '/employer/invitations',
  },
];

export function EmployerHomePage(): ReactNode {
  return (
    <>
      <PageHeader
        title="Кабинет работодателя"
        subtitle="Потребность → подграф навыков → подборка → приглашение"
        extra={
          <Button variant="outline" asChild>
            <Link to="/skills/graph/be">
              <Share2 aria-hidden="true" />
              Граф навыков
            </Link>
          </Button>
        }
      />

      <section className="rounded-xl border border-border bg-card shadow-card">
        <div className="flex flex-col items-center gap-4 px-6 py-12 text-center">
          <Illustration name="need-empty" height={180} />
          <h2 className="text-balance text-xl font-semibold">Опишите потребность</h2>
          <p className="max-w-[520px] text-sm leading-relaxed text-muted-foreground">
            Кого ищете и какие задачи ждут человека в первые три месяца. Из текста соберём
            подграф навыков — его можно поправить руками — и покажем, какие задания это проверят.
          </p>
          <Button variant="brand" size="lg" asChild>
            <Link to="/employer/needs">
              Описать потребность
              <ArrowRight aria-hidden="true" />
            </Link>
          </Button>
          <p className="text-xs text-muted-foreground">
            Форма потребности и разбор текста появятся на следующем шаге разработки
          </p>
        </div>
      </section>

      <StaggerGrid className="mt-4 grid gap-4 md:grid-cols-3">
        {STEPS.map((step) => {
          const Icon = step.icon;
          return (
            <StaggerItem key={step.title}>
              <Link
                to={step.to}
                className="group flex h-full flex-col gap-3 rounded-lg border border-border bg-card p-5 shadow-card transition-[border-color,box-shadow,transform] duration-150 hover:-translate-y-0.5 hover:shadow-overlay"
              >
                <span className="flex size-10 items-center justify-center rounded-md bg-primary-tint text-primary-deep">
                  <Icon className="size-5" aria-hidden="true" />
                </span>
                <span className="text-sm font-semibold group-hover:text-primary-deep">{step.title}</span>
                <span className="text-sm leading-relaxed text-muted-foreground">{step.text}</span>
              </Link>
            </StaggerItem>
          );
        })}
      </StaggerGrid>
    </>
  );
}
