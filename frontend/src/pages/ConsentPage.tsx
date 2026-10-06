import { useMutation } from '@tanstack/react-query';
import { ChevronDown, ShieldCheck } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  POLICY_SUMMARY,
  POLICY_TITLE,
  POLICY_VERSION,
  PrivacyPolicyText,
} from '@/content/privacy-policy.ru';
import { giveConsent } from '@/shared/api/endpoints/auth';
import { useAuth } from '@/shared/auth/AuthContext';
import { homePathFor } from '@/shared/auth/roles';
import { cn } from '@/shared/lib/cn';
import { useDocumentTitle } from '@/shared/lib/useDocumentTitle';
import { Button } from '@/shared/ui/button';
import { Checkbox } from '@/shared/ui/checkbox';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/shared/ui/collapsible';
import { Label } from '@/shared/ui/label';
import { ScrollArea } from '@/shared/ui/scroll-area';

/**
 * Экран согласия 152-ФЗ: краткая выжимка, полный текст политики (версия
 * POLICY_VERSION) под раскрывающимся блоком, два чекбокса — обязательное согласие
 * на обработку ПДн и необязательное на публикацию профиля работодателям —
 * → `POST /auth/consent {policy_version, publish}`. Без согласия на обработку
 * кабинет недоступен (guard RequireOnboarded); при смене версии политики экран
 * показывается повторно.
 *
 * Чекбокс публикации никогда не предотмечен: согласие по ст. 9 152-ФЗ — активное
 * действие. При повторном согласии подставляется прежний выбор пользователя.
 */
export function ConsentPage(): ReactNode {
  const { user, activeRole, setUser, logout } = useAuth();
  const navigate = useNavigate();
  const [pdn, setPdn] = useState(false);
  const [publish, setPublish] = useState(user?.consent.publish_at != null);
  const [policyOpen, setPolicyOpen] = useState(false);
  useDocumentTitle('Согласие на обработку данных');

  const mutation = useMutation({
    mutationFn: () => giveConsent({ policy_version: POLICY_VERSION, publish }),
    onSuccess: (me) => {
      setUser(me);
      navigate(homePathFor(activeRole), { replace: true });
    },
  });

  const isCandidate = activeRole === 'candidate';

  return (
    <div className="flex min-h-screen items-start justify-center bg-background px-4 py-10">
      <div className="flex w-full max-w-[760px] flex-col gap-5">
        <div className="flex flex-col items-center gap-2 text-center">
          <span className="flex size-12 items-center justify-center rounded-full bg-primary-tint text-primary-deep">
            <ShieldCheck className="size-6" aria-hidden="true" />
          </span>
          <h1 className="text-balance text-2xl font-semibold">Согласие на обработку данных</h1>
          <p className="max-w-lg text-sm text-muted-foreground">
            Чтобы продолжить, подтвердите, что ознакомились с политикой и согласны на обработку
            персональных данных. Это требование 152-ФЗ.
          </p>
        </div>

        <section className="rounded-xl border border-border bg-card p-6 shadow-card">
          <h2 className="mb-3 text-base font-semibold">Коротко о главном</h2>
          <ul className="space-y-2 text-sm leading-relaxed">
            {POLICY_SUMMARY.map((line) => (
              <li key={line} className="flex gap-2">
                <span className="mt-2 size-1.5 shrink-0 rounded-full bg-primary" aria-hidden="true" />
                <span>{line}</span>
              </li>
            ))}
          </ul>

          <Collapsible open={policyOpen} onOpenChange={setPolicyOpen} className="mt-4">
            <CollapsibleTrigger asChild>
              <button
                type="button"
                className="flex w-full items-center justify-between rounded-md border border-border bg-muted/60 px-4 py-2.5 text-sm font-medium outline-none transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              >
                <span>
                  {POLICY_TITLE}{' '}
                  <span className="font-normal text-muted-foreground">· версия {POLICY_VERSION}</span>
                </span>
                <ChevronDown
                  aria-hidden="true"
                  className={cn('size-4 transition-transform duration-150', policyOpen && 'rotate-180')}
                />
              </button>
            </CollapsibleTrigger>
            <CollapsibleContent>
              <ScrollArea className="mt-2 h-[360px] rounded-md border border-border px-4">
                <PrivacyPolicyText />
              </ScrollArea>
            </CollapsibleContent>
          </Collapsible>
        </section>

        <section className="flex flex-col gap-4 rounded-xl border border-border bg-card p-6 shadow-card">
          <div className="flex items-start gap-3">
            <Checkbox
              id="consent-pdn"
              checked={pdn}
              onCheckedChange={(value) => setPdn(value === true)}
              className="mt-0.5"
            />
            <Label htmlFor="consent-pdn" className="cursor-pointer text-sm leading-relaxed">
              Я ознакомлен(а) с политикой версии {POLICY_VERSION} и даю согласие на обработку моих
              персональных данных в указанных целях.{' '}
              <span className="text-primary-deep">Обязательно</span>
            </Label>
          </div>
          <div className="flex items-start gap-3">
            <Checkbox
              id="consent-publish"
              checked={publish}
              onCheckedChange={(value) => setPublish(value === true)}
              className="mt-0.5"
            />
            <Label htmlFor="consent-publish" className="cursor-pointer text-sm leading-relaxed">
              {isCandidate
                ? 'Разрешаю показывать мой профиль работодателям под псевдонимом: категорию, подтверждённые навыки и обезличенные свершения. Контакты раскрываются только после того, как я приму приглашение.'
                : 'Разрешаю показывать название компании и описание потребностей кандидатам в приглашениях и вакансиях.'}{' '}
              <span className="text-muted-foreground">
                {isCandidate ? 'Рекомендуем включить — без этого работодатели вас не увидят. ' : ''}
                Можно изменить позже в настройках
              </span>
            </Label>
          </div>

          <div className="mt-2 flex flex-col-reverse items-center justify-between gap-3 sm:flex-row">
            <p className="text-xs text-muted-foreground">
              Аккаунт: <span className="font-medium text-foreground">{user?.email ?? '—'}</span>
              {' · '}
              <button type="button" className="text-primary-deep hover:underline" onClick={logout}>
                выйти
              </button>
            </p>
            <Button
              size="lg"
              disabled={!pdn || mutation.isPending}
              onClick={() => mutation.mutate()}
              className="w-full sm:w-auto"
            >
              {mutation.isPending ? 'Сохраняем…' : 'Подтвердить и продолжить'}
            </Button>
          </div>
        </section>
      </div>
    </div>
  );
}
