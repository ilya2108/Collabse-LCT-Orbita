import { useMutation, useQuery } from '@tanstack/react-query';
import { Copy, ExternalLink, Send } from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import { createTelegramLinkToken, fetchTelegramStatus } from '@/shared/api/endpoints/telegram';
import { notifyApiError, notifySuccess } from '@/shared/api/feedback';
import type { TelegramLinkToken } from '@/shared/api/types';
import { TELEGRAM_BOT_NAME } from '@/shared/config/crm';
import { formatDate } from '@/shared/lib/format';
import { Button } from '@/shared/ui/button';
import { SectionCard } from '@/shared/ui/detail-blocks';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/ui/dialog';
import { Input } from '@/shared/ui/input';
import { Label } from '@/shared/ui/label';
import { StatusBadge } from '@/shared/ui/status-badge';

/**
 * Блок «Телеграм-бот ФСП.Орбита» в настройках обеих ролей: привязка по коду
 * (deep-link + одноразовый код), статус привязки и пояснение про недельный дайджест.
 * Один бот — разные роли: текст дайджеста зависит от роли пользователя.
 */
export function TelegramBotCard({ role }: { role: 'candidate' | 'employer' }): ReactNode {
  const [token, setToken] = useState<TelegramLinkToken | null>(null);

  const statusQuery = useQuery({
    queryKey: ['telegram', 'me'],
    queryFn: ({ signal }) => fetchTelegramStatus(signal),
    // пока открыт диалог привязки — опрашиваем статус, чтобы поймать «Start» в боте
    refetchInterval: token ? 4000 : false,
  });

  const linked = statusQuery.data?.linked ?? false;
  useEffect(() => {
    // привязка состоялась, пока диалог был открыт — закрываем его
    if (token && linked) {
      setToken(null);
      notifySuccess('Телеграм привязан', 'Бот будет присылать уведомления и недельный дайджест');
    }
  }, [token, linked]);

  const tokenMutation = useMutation({
    mutationFn: createTelegramLinkToken,
    onSuccess: (result) => setToken(result),
    onError: (error) => notifyApiError(error, 'Не удалось получить код привязки'),
  });

  const digestText =
    role === 'candidate'
      ? 'Раз в неделю бот пришлёт дайджест: новые вакансии под вашу категорию, входящие приглашения и узлы, которые скоро «потускнеют».'
      : 'Раз в неделю бот пришлёт дайджест: новые кандидаты под ваши потребности, движение по воронке найма и ответы на приглашения.';

  const copyCode = async (): Promise<void> => {
    if (!token) return;
    try {
      await navigator.clipboard.writeText(token.code);
      notifySuccess('Код скопирован');
    } catch {
      notifyApiError(new Error('Буфер обмена недоступен — скопируйте код вручную'));
    }
  };

  return (
    <SectionCard
      title="Телеграм-бот ФСП.Орбита"
      extra={
        linked ? (
          <StatusBadge status="success">привязан</StatusBadge>
        ) : (
          <StatusBadge status="draft">не привязан</StatusBadge>
        )
      }
    >
      <div className="flex flex-col gap-3">
        <p className="text-sm leading-relaxed text-muted-foreground">
          Один бот для кандидатов, HR и нанимающих менеджеров — роль определяется после
          привязки. {digestText}
        </p>
        {linked ? (
          <p className="text-sm">
            Привязан аккаунт{' '}
            <span className="font-medium">{statusQuery.data?.username_masked ?? '—'}</span>
            {statusQuery.data?.linked_at ? ` · с ${formatDate(statusQuery.data.linked_at)}` : ''}.
            Недельный дайджест {statusQuery.data?.digest_enabled ? 'включён' : 'выключен'} —
            управлять им можно командой /digest в боте.
          </p>
        ) : (
          <div>
            <Button
              variant="brand"
              disabled={tokenMutation.isPending}
              onClick={() => tokenMutation.mutate()}
            >
              <Send aria-hidden="true" />
              {tokenMutation.isPending ? 'Получаем код…' : 'Привязать Телеграм'}
            </Button>
          </div>
        )}
      </div>

      <Dialog open={token !== null} onOpenChange={(next) => (next ? undefined : setToken(null))}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Привязка телеграм-бота</DialogTitle>
            <DialogDescription>
              Откройте бота по ссылке — код подставится сам. Если открываете {TELEGRAM_BOT_NAME}{' '}
              вручную, отправьте ему код сообщением.
            </DialogDescription>
          </DialogHeader>
          {token ? (
            <div className="grid gap-3">
              <Button variant="brand" asChild>
                <a href={token.deep_link} target="_blank" rel="noreferrer">
                  <ExternalLink aria-hidden="true" />
                  Открыть {TELEGRAM_BOT_NAME}
                </a>
              </Button>
              <div className="grid gap-1.5">
                <Label>Код привязки (действует 10 минут)</Label>
                <div className="flex items-center gap-2">
                  <Input readOnly value={token.code} className="text-center font-semibold tracking-widest" />
                  <Button
                    variant="outline"
                    size="icon"
                    aria-label="Скопировать код"
                    onClick={() => void copyCode()}
                  >
                    <Copy aria-hidden="true" />
                  </Button>
                </div>
              </div>
              <p className="text-xs text-muted-foreground" role="status">
                Ждём подтверждения в боте — статус обновится сам.
              </p>
            </div>
          ) : null}
          <DialogFooter>
            <Button variant="outline" onClick={() => setToken(null)}>
              Закрыть
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </SectionCard>
  );
}
