import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Illustration, type IllustrationName } from '@/shared/illustrations';
import { Button } from '@/shared/ui/button';
import { PageHeader } from '@/shared/ui/page-header';

/**
 * Пустое состояние раздела, который появится в следующие дни: заголовок страницы,
 * иллюстрация, объяснение «что здесь будет» и один следующий шаг.
 */
export interface StubPageProps {
  title: string;
  subtitle?: string;
  illustration?: IllustrationName;
  /** Что здесь будет — одно-два предложения. */
  description: ReactNode;
  /** Следующий шаг: ссылка внутри кабинета. */
  action?: { label: string; to: string };
  /** Дополнительный контент под пустым состоянием (например, чек-лист). */
  children?: ReactNode;
}

export function StubPage({
  title,
  subtitle,
  illustration = 'box-empty',
  description,
  action,
  children,
}: StubPageProps): ReactNode {
  return (
    <>
      <PageHeader title={title} subtitle={subtitle} />
      <section className="rounded-lg border border-border bg-card shadow-card">
        <div className="flex flex-col items-center gap-3 px-6 py-12 text-center">
          <Illustration name={illustration} height={160} />
          <p className="text-base font-semibold text-balance">Пока здесь пусто</p>
          <div className="max-w-[480px] text-sm leading-relaxed text-muted-foreground">
            {description}
          </div>
          {action ? (
            <Button asChild className="mt-1">
              <Link to={action.to}>{action.label}</Link>
            </Button>
          ) : null}
        </div>
      </section>
      {children}
    </>
  );
}
