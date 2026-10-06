import { Building2 } from 'lucide-react';
import type { ReactNode } from 'react';
import { TelegramBotCard } from '@/pages/settings/TelegramBotCard';
import { MiniCard, SectionCard } from '@/shared/ui/detail-blocks';
import { PageHeader } from '@/shared/ui/page-header';

/**
 * Настройки работодателя: привязка телеграм-бота (дайджест кандидатов под
 * потребности, уведомления об ответах) и ссылки на управление компанией.
 */
export function EmployerSettingsPage(): ReactNode {
  return (
    <>
      <PageHeader title="Настройки" subtitle="Телеграм-бот и управление компанией" />
      <div className="grid items-start gap-4 lg:grid-cols-2">
        <TelegramBotCard role="employer" />
        <SectionCard title="Компания и доступы">
          <p className="mb-3 text-sm leading-relaxed text-muted-foreground">
            Профиль компании, участники и приглашения HR или нанимающих менеджеров — на странице
            «Компания». Приглашённые через Telegram работают с ботом без входа на платформу.
          </p>
          <MiniCard
            to="/employer/company"
            icon={Building2}
            title="Компания"
            description="Профиль, участники, приглашение HR"
          />
        </SectionCard>
      </div>
    </>
  );
}
