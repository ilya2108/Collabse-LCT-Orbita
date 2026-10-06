import { useQuery } from '@tanstack/react-query';
import { Send } from 'lucide-react';
import type { ReactNode } from 'react';
import { TELEGRAM_BOT_NAME } from '@/shared/config/crm';
import { useDocumentTitle } from '@/shared/lib/useDocumentTitle';
import { Button } from '@/shared/ui/button';
import { EmptyState, ErrorState, LoadingState } from '@/shared/ui/states';
import { fetchTmaMe, isTmaNotLinked } from './api';
import { TmaInvitationsScreen } from './TmaInvitationsScreen';
import { TmaNeedsScreen } from './TmaNeedsScreen';

/**
 * Стартовый экран мини-аппа: GET /tma/me определяет актора и экран —
 * кандидат видит «Приглашения», работодатель и участник компании — «Потребности».
 * Непривязанный чат (403 tma_not_linked) — экран «Привяжите бота» с инструкцией.
 */

function TmaNotLinkedScreen(): ReactNode {
  const botUrl = `https://t.me/${TELEGRAM_BOT_NAME.slice(1)}`;
  return (
    <section className="rounded-lg border border-border bg-card shadow-card">
      <EmptyState
        illustration="box-empty"
        title="Сначала привяжите бота"
        description={
          <span className="block text-left">
            Этот чат ещё не привязан к аккаунту ФСП.Орбита. Как привязать:
            <ol className="mt-2 list-decimal space-y-1 pl-5">
              <li>Откройте кабинет на сайте → «Настройки» → «Телеграм-бот».</li>
              <li>Нажмите «Привязать» и скопируйте одноразовый код.</li>
              <li>
                Отправьте боту {TELEGRAM_BOT_NAME} команду /start с кодом — и откройте
                мини-апп заново.
              </li>
            </ol>
          </span>
        }
        actions={
          <Button variant="brand" size="lg" asChild>
            <a href={botUrl} target="_blank" rel="noreferrer">
              <Send aria-hidden="true" />
              Открыть бота
            </a>
          </Button>
        }
      />
    </section>
  );
}

export function TmaHomePage(): ReactNode {
  useDocumentTitle('Мини-апп');

  const meQuery = useQuery({
    queryKey: ['tma', 'me'],
    queryFn: () => fetchTmaMe(),
    retry: false,
  });

  if (meQuery.isPending) return <LoadingState rows={6} />;
  if (meQuery.isError) {
    if (isTmaNotLinked(meQuery.error)) return <TmaNotLinkedScreen />;
    return <ErrorState error={meQuery.error} onRetry={() => void meQuery.refetch()} />;
  }

  return meQuery.data.kind === 'candidate' ? (
    <TmaInvitationsScreen me={meQuery.data} />
  ) : (
    <TmaNeedsScreen me={meQuery.data} />
  );
}
