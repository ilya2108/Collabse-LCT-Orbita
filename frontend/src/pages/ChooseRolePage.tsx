import { useMutation } from '@tanstack/react-query';
import { Briefcase, Check, UserRound, type LucideIcon } from 'lucide-react';
import { motion } from 'motion/react';
import { useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { setRole } from '@/shared/api/endpoints/auth';
import { isApiError } from '@/shared/api/errors';
import type { SelectableRole } from '@/shared/api/types';
import { useAuth } from '@/shared/auth/AuthContext';
import { ROLE_DESCRIPTIONS, ROLE_LABELS, SELECTABLE_ROLES } from '@/shared/auth/roles';
import { cn } from '@/shared/lib/cn';
import { staggerContainer, staggerItem } from '@/shared/lib/motion';
import { useDocumentTitle } from '@/shared/lib/useDocumentTitle';
import { Button } from '@/shared/ui/button';

/**
 * Экран выбора роли при первом входе: две карточки-переключателя (кандидат /
 * работодатель, кнопки с aria-pressed — у ARIA-radio своя клавиатурная модель,
 * которой обычные кнопки не дают) → `POST /auth/role`. Роль хранится в БД и после
 * выбора не меняется (409 role_already_set — перечитываем профиль и идём дальше).
 */

const ROLE_ICONS: Record<SelectableRole, LucideIcon> = {
  candidate: UserRound,
  employer: Briefcase,
};

export function ChooseRolePage(): ReactNode {
  const { user, setUser, refreshUser, logout } = useAuth();
  const navigate = useNavigate();
  const [selected, setSelected] = useState<SelectableRole>('candidate');
  useDocumentTitle('Выбор роли');

  const mutation = useMutation({
    mutationFn: (role: SelectableRole) => setRole({ role }),
    onSuccess: (me) => {
      setUser(me);
      navigate('/consent', { replace: true });
    },
    onError: async (error) => {
      // Роль уже выбрана в другой вкладке — профиль обновится, guard уведёт дальше.
      if (isApiError(error) && error.code === 'role_already_set') {
        await refreshUser().catch(() => undefined);
      }
    },
  });

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4 py-10">
      <motion.div
        variants={staggerContainer}
        initial="hidden"
        animate="visible"
        className="flex w-full max-w-[720px] flex-col gap-6"
      >
        <motion.div variants={staggerItem} className="flex flex-col items-center gap-2 text-center">
          <img src="/favicon.svg" alt="" width={48} height={48} />
          <h1 className="text-balance text-2xl font-semibold">Кто вы в Орбите?</h1>
          <p className="max-w-md text-sm text-muted-foreground">
            Роль определяет кабинет и доступные действия. Выбор делается один раз; для другой
            роли понадобится отдельный аккаунт.
          </p>
        </motion.div>

        <div role="group" aria-label="Роль" className="grid gap-4 sm:grid-cols-2">
          {SELECTABLE_ROLES.map((role) => {
            const Icon = ROLE_ICONS[role];
            const active = selected === role;
            return (
              <motion.button
                key={role}
                variants={staggerItem}
                type="button"
                aria-pressed={active}
                onClick={() => setSelected(role)}
                className={cn(
                  'relative flex flex-col items-start gap-3 rounded-xl border bg-card p-6 text-left outline-none',
                  'transition-[border-color,box-shadow,transform] duration-150 hover:-translate-y-0.5 hover:shadow-overlay',
                  'focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
                  active ? 'border-primary shadow-card ring-1 ring-primary' : 'border-border',
                )}
              >
                <span
                  className={cn(
                    'flex size-11 items-center justify-center rounded-md',
                    active ? 'bg-primary text-primary-foreground' : 'bg-primary-tint text-primary-deep',
                  )}
                >
                  <Icon className="size-5" aria-hidden="true" />
                </span>
                <span className="text-base font-semibold">{ROLE_LABELS[role]}</span>
                <span className="text-sm leading-relaxed text-muted-foreground">
                  {ROLE_DESCRIPTIONS[role]}
                </span>
                {active ? (
                  <span
                    className="absolute right-4 top-4 flex size-6 items-center justify-center rounded-full bg-primary text-primary-foreground"
                    aria-hidden="true"
                  >
                    <Check className="size-3.5" />
                  </span>
                ) : null}
              </motion.button>
            );
          })}
        </div>

        <motion.div
          variants={staggerItem}
          className="flex flex-col-reverse items-center justify-between gap-3 sm:flex-row"
        >
          <p className="text-xs text-muted-foreground">
            Вошли как <span className="font-medium text-foreground">{user?.email ?? '—'}</span>
            {' · '}
            <button type="button" className="text-primary-deep hover:underline" onClick={logout}>
              выйти
            </button>
          </p>
          <Button
            size="lg"
            disabled={mutation.isPending}
            onClick={() => mutation.mutate(selected)}
            className="w-full sm:w-auto"
          >
            {mutation.isPending ? 'Сохраняем…' : `Продолжить как ${ROLE_LABELS[selected].toLowerCase()}`}
          </Button>
        </motion.div>
      </motion.div>
    </div>
  );
}
