import { useMutation, useQuery } from '@tanstack/react-query';
import { LogIn, Send } from 'lucide-react';
import type { ReactNode } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { acceptCompanyInvite, fetchPublicInvite } from '@/shared/api/endpoints/employers';
import { notifyApiError, notifySuccess } from '@/shared/api/feedback';
import { useAuth } from '@/shared/auth/AuthContext';
import { MEMBER_ROLE_LABELS, TELEGRAM_BOT_NAME } from '@/shared/config/crm';
import { useDocumentTitle } from '@/shared/lib/useDocumentTitle';
import { Button } from '@/shared/ui/button';
import { ErrorState, LoadingState } from '@/shared/ui/states';

/**
 * Публичная страница принятия приглашения в компанию: /employer/invite/{token}.
 * Информация о приглашении доступна без входа (GET /employers/company/invites/{token});
 * принятие привязывает аккаунт и дописывает роль employer. Тот же токен принимает
 * телеграм-бот ФСП.Орбита — без входа на платформу.
 */
export function EmployerInviteAcceptPage(): ReactNode {
  const { token = '' } = useParams();
  const { phase, login, refreshUser } = useAuth();
  const navigate = useNavigate();
  useDocumentTitle('Приглашение в компанию');

  const inviteQuery = useQuery({
    queryKey: ['employers', 'invite', token],
    queryFn: ({ signal }) => fetchPublicInvite(token, signal),
    retry: false,
  });

  const acceptMutation = useMutation({
    mutationFn: () => acceptCompanyInvite(token),
    onSuccess: async () => {
      notifySuccess('Приглашение принято', 'Теперь вам доступен кабинет компании');
      await refreshUser().catch(() => undefined);
      navigate('/employer', { replace: true });
    },
    onError: (error) => notifyApiError(error, 'Не удалось принять приглашение'),
  });

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="flex w-full max-w-[440px] flex-col gap-4 rounded-xl border border-border bg-card p-8 text-center shadow-card">
        <img src="/favicon.svg" alt="ФСП.Орбита" width={48} height={48} className="mx-auto" />
        {inviteQuery.isPending ? (
          <LoadingState rows={3} card={false} />
        ) : inviteQuery.isError ? (
          <ErrorState
            error={inviteQuery.error}
            title="Приглашение не найдено или уже использовано"
            onRetry={() => void inviteQuery.refetch()}
            compact
          />
        ) : (
          <>
            <h1 className="text-balance text-xl font-semibold">
              Вас приглашают в «{inviteQuery.data.company_name}»
            </h1>
            <p className="text-sm leading-relaxed text-muted-foreground">
              Роль: <b>{MEMBER_ROLE_LABELS[inviteQuery.data.role]}</b>. После принятия вам будут
              доступны потребности компании, подборка и воронка найма.
            </p>
            {phase === 'ready' ? (
              <Button
                variant="brand"
                size="lg"
                disabled={acceptMutation.isPending}
                onClick={() => acceptMutation.mutate()}
              >
                <Send aria-hidden="true" />
                {acceptMutation.isPending ? 'Принимаем…' : 'Принять приглашение'}
              </Button>
            ) : (
              <Button
                variant="brand"
                size="lg"
                onClick={() => login(window.location.pathname)}
                disabled={phase === 'initializing'}
              >
                <LogIn aria-hidden="true" />
                Войти, чтобы принять
              </Button>
            )}
            <p className="text-xs leading-relaxed text-muted-foreground">
              Не хотите заводить аккаунт сейчас? Отправьте код приглашения телеграм-боту{' '}
              {TELEGRAM_BOT_NAME} — он работает без входа на платформу.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
