import type { ReactNode } from 'react';
import { useAuth } from '@/shared/auth/AuthContext';
import { formatDate } from '@/shared/lib/format';
import { TelegramBotCard } from '@/pages/settings/TelegramBotCard';
import { DL, DLRow, SectionCard } from '@/shared/ui/detail-blocks';
import { PageHeader } from '@/shared/ui/page-header';
import { StatusBadge } from '@/shared/ui/status-badge';

/**
 * Настройки кандидата: привязка телеграм-бота (дайджест вакансий и приглашений)
 * и сводка согласий 152-ФЗ. Управление приватностью по компаниям и удаление
 * аккаунта — следующие дни плана.
 */
export function CandidateSettingsPage(): ReactNode {
  const { user } = useAuth();
  return (
    <>
      <PageHeader
        title="Настройки"
        subtitle="Телеграм-бот, согласия и приватность профиля"
      />
      <div className="grid items-start gap-4 lg:grid-cols-2">
        <TelegramBotCard role="candidate" />
        <SectionCard
          title="Согласия и приватность"
          extra={
            user?.consent.publish_at ? (
              <StatusBadge status="success">профиль опубликован</StatusBadge>
            ) : (
              <StatusBadge status="draft">профиль скрыт</StatusBadge>
            )
          }
        >
          <DL labelWidth={190}>
            <DLRow label="Обработка ПДн">
              {user?.consent.pdn_at
                ? `согласие от ${formatDate(user.consent.pdn_at)}`
                : 'согласие не дано'}
            </DLRow>
            <DLRow label="Версия политики">{user?.consent.policy_version ?? '—'}</DLRow>
            <DLRow label="Показ работодателям">
              {user?.consent.publish_at
                ? `разрешён с ${formatDate(user.consent.publish_at)}`
                : 'запрещён — приглашения не приходят'}
            </DLRow>
          </DL>
          <p className="mt-3 border-t border-border pt-3 text-xs leading-relaxed text-muted-foreground">
            Скрытие профиля от конкретных компаний, отзыв согласий и удаление аккаунта вместе с
            персональными данными появятся здесь на следующих шагах разработки.
          </p>
        </SectionCard>
      </div>
    </>
  );
}
