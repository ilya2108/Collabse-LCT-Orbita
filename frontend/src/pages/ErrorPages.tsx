import type { ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '@/shared/auth/AuthContext';
import { homePathFor } from '@/shared/auth/roles';
import { Illustration, type IllustrationName } from '@/shared/illustrations';
import { useDocumentTitle } from '@/shared/lib/useDocumentTitle';
import { Button } from '@/shared/ui/button';

/** Полноэкранные 403/404 с иллюстрацией и кнопкой «В кабинет». */

function ErrorScreen({
  illustration,
  title,
  description,
}: {
  illustration: IllustrationName;
  title: string;
  description: string;
}): ReactNode {
  const navigate = useNavigate();
  const { activeRole } = useAuth();
  useDocumentTitle(title);
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 px-6 py-16 text-center">
      <Illustration name={illustration} height={200} />
      <h1 className="text-balance text-xl font-semibold">{title}</h1>
      <p className="max-w-md text-sm text-muted-foreground">{description}</p>
      <Button onClick={() => navigate(homePathFor(activeRole))}>В кабинет</Button>
    </div>
  );
}

export function ForbiddenPage(): ReactNode {
  return (
    <ErrorScreen
      illustration="error-403"
      title="Нет доступа"
      description="Этот раздел недоступен для вашей роли. Кандидат и работодатель видят разные кабинеты."
    />
  );
}

export function NotFoundPage(): ReactNode {
  return (
    <ErrorScreen
      illustration="error-404"
      title="Страница не найдена"
      description="Такой страницы нет или она была перемещена."
    />
  );
}
