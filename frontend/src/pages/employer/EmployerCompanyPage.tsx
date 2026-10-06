import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Building2, Check, Copy, Send, UserMinus } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import {
  fetchCompanyMembers,
  fetchMyCompany,
  inviteMember,
  revokeMember,
  updateMyCompany,
} from '@/shared/api/endpoints/employers';
import { listIndustries } from '@/shared/api/endpoints/skills';
import { notifyApiError, notifySuccess } from '@/shared/api/feedback';
import type {
  CompanyOut,
  CompanyUpdate,
  InviteChannel,
  MemberInviteResult,
  MemberOut,
} from '@/shared/api/types';
import {
  COMPANY_KIND_LABELS,
  MEMBER_ROLE_LABELS,
  MEMBER_STATUS_LABELS,
  MEMBER_STATUS_STATUS,
  TELEGRAM_BOT_NAME,
} from '@/shared/config/crm';
import { formatDate, formatText } from '@/shared/lib/format';
import { Button } from '@/shared/ui/button';
import { ConfirmDialog } from '@/shared/ui/confirm-dialog';
import { DL, DLRow, SectionCard } from '@/shared/ui/detail-blocks';
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
import { PageHeader } from '@/shared/ui/page-header';
import { SegmentedControl } from '@/shared/ui/segmented';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/ui/select';
import { ErrorState, LoadingState } from '@/shared/ui/states';
import { StatusBadge } from '@/shared/ui/status-badge';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/ui/table';
import { Textarea } from '@/shared/ui/textarea';

/**
 * «Компания»: профиль (личный/корпоративный), участники и приглашение HR
 * или нанимающего менеджера по ссылке — в том числе через Telegram, без входа
 * на платформу (контракты агента ядра: /employers/me/company*).
 */

async function copyText(value: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(value);
    notifySuccess('Скопировано в буфер обмена');
  } catch {
    notifyApiError(new Error('Буфер обмена недоступен — выделите и скопируйте текст вручную'));
  }
}

// ---------------------------------------------------------------------------
// Диалог редактирования профиля компании
// ---------------------------------------------------------------------------

function EditCompanyDialog({
  company,
  open,
  onClose,
}: {
  company: CompanyOut;
  open: boolean;
  onClose: () => void;
}): ReactNode {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<CompanyUpdate>({});
  const industriesQuery = useQuery({ queryKey: ['skills', 'industries'], queryFn: ({ signal }) => listIndustries(signal) });

  const mutation = useMutation({
    mutationFn: (body: CompanyUpdate) => updateMyCompany(body),
    onSuccess: (updated) => {
      queryClient.setQueryData(['employers', 'company'], updated);
      notifySuccess('Профиль компании обновлён');
      onClose();
    },
    onError: (error) => notifyApiError(error, 'Не удалось сохранить компанию'),
  });

  const value = <K extends keyof CompanyUpdate>(key: K, fallback: string | null): string =>
    (form[key] as string | null | undefined) ?? fallback ?? '';
  const set = (key: keyof CompanyUpdate) => (v: string) =>
    setForm((prev) => ({ ...prev, [key]: v === '' ? null : v }));

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? undefined : onClose())}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Профиль компании</DialogTitle>
          <DialogDescription>
            Название и контакты видит кандидат после того, как примет приглашение.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor="company-name">Название</Label>
            <Input
              id="company-name"
              value={value('name', company.name)}
              onChange={(e) => set('name')(e.target.value)}
            />
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="grid gap-1.5">
              <Label htmlFor="company-inn">ИНН</Label>
              <Input
                id="company-inn"
                value={value('inn', company.inn)}
                onChange={(e) => set('inn')(e.target.value)}
              />
            </div>
            <div className="grid gap-1.5">
              <Label>Отрасль</Label>
              <Select
                value={value('industry_code', company.industry_code) || undefined}
                onValueChange={set('industry_code')}
              >
                <SelectTrigger aria-label="Отрасль">
                  <SelectValue placeholder="Выберите отрасль" />
                </SelectTrigger>
                <SelectContent>
                  {(industriesQuery.data ?? []).map((industry) => (
                    <SelectItem key={industry.code} value={industry.code}>
                      {industry.title}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="company-site">Сайт</Label>
            <Input
              id="company-site"
              value={value('website', company.website)}
              onChange={(e) => set('website')(e.target.value)}
              placeholder="https://"
            />
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="grid gap-1.5">
              <Label htmlFor="company-email">Email для связи</Label>
              <Input
                id="company-email"
                type="email"
                value={value('contact_email', company.contact_email)}
                onChange={(e) => set('contact_email')(e.target.value)}
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="company-phone">Телефон</Label>
              <Input
                id="company-phone"
                value={value('contact_phone', company.contact_phone)}
                onChange={(e) => set('contact_phone')(e.target.value)}
              />
            </div>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="company-descr">Описание</Label>
            <Textarea
              id="company-descr"
              rows={3}
              value={value('description', company.description)}
              onChange={(e) => set('description')(e.target.value)}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Отмена
          </Button>
          <Button disabled={mutation.isPending} onClick={() => mutation.mutate(form)}>
            {mutation.isPending ? 'Сохраняем…' : 'Сохранить'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Диалог «Пригласить HR»
// ---------------------------------------------------------------------------

function InviteMemberDialog({ open, onClose }: { open: boolean; onClose: () => void }): ReactNode {
  const queryClient = useQueryClient();
  const [role, setRole] = useState<'hr' | 'hiring_manager'>('hr');
  const [channel, setChannel] = useState<InviteChannel>('telegram');
  const [fullName, setFullName] = useState('');
  const [contact, setContact] = useState('');
  const [result, setResult] = useState<MemberInviteResult | null>(null);

  const mutation = useMutation({
    mutationFn: inviteMember,
    onSuccess: (res) => {
      setResult(res);
      void queryClient.invalidateQueries({ queryKey: ['employers', 'members'] });
      void queryClient.invalidateQueries({ queryKey: ['employers', 'company'] });
    },
    onError: (error) => notifyApiError(error, 'Не удалось создать приглашение'),
  });

  const close = (): void => {
    setResult(null);
    setFullName('');
    setContact('');
    mutation.reset();
    onClose();
  };

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? undefined : close())}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Пригласить в компанию</DialogTitle>
          <DialogDescription>
            HR и нанимающие менеджеры работают с потребностями и воронкой найма вашей компании.
          </DialogDescription>
        </DialogHeader>

        {result ? (
          <div className="grid gap-3">
            <p className="text-sm">
              Приглашение создано. Ссылка и код показываются <b>один раз</b> — передайте их
              приглашённому.
            </p>
            <div className="grid gap-1.5">
              <Label>Ссылка-приглашение</Label>
              <div className="flex items-center gap-2">
                <Input readOnly value={result.invite_url} className="font-medium" />
                <Button
                  variant="outline"
                  size="icon"
                  aria-label="Скопировать ссылку"
                  onClick={() => void copyText(result.invite_url)}
                >
                  <Copy aria-hidden="true" />
                </Button>
              </div>
            </div>
            <div className="grid gap-1.5">
              <Label>Код для телеграм-бота</Label>
              <div className="flex items-center gap-2">
                <Input readOnly value={result.invite_token} />
                <Button
                  variant="outline"
                  size="icon"
                  aria-label="Скопировать код"
                  onClick={() => void copyText(result.invite_token)}
                >
                  <Copy aria-hidden="true" />
                </Button>
              </div>
            </div>
            <p className="rounded-md bg-primary-tint p-3 text-xs leading-relaxed text-primary-deep">
              Вариант через Telegram работает без входа на платформу: приглашённый отправляет код
              боту {TELEGRAM_BOT_NAME} и получает подборки и уведомления прямо в чате. Войти на
              платформу можно позже — доступ сохранится.
            </p>
            <DialogFooter>
              <Button onClick={close}>Готово</Button>
            </DialogFooter>
          </div>
        ) : (
          <div className="grid gap-3">
            <div className="grid gap-1.5">
              <Label>Роль</Label>
              <SegmentedControl
                label="Роль приглашённого"
                value={role}
                onChange={setRole}
                options={[
                  { value: 'hr', label: 'HR' },
                  { value: 'hiring_manager', label: 'Нанимающий менеджер' },
                ]}
              />
            </div>
            <div className="grid gap-1.5">
              <Label>Канал приглашения</Label>
              <SegmentedControl
                label="Канал приглашения"
                value={channel}
                onChange={setChannel}
                options={[
                  { value: 'telegram', label: 'Telegram' },
                  { value: 'email', label: 'Эл. почта' },
                ]}
              />
              {channel === 'telegram' ? (
                <p className="text-xs text-muted-foreground">
                  Работает без входа на платформу — бот {TELEGRAM_BOT_NAME} покажет приглашение по
                  коду.
                </p>
              ) : null}
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="invite-name">Имя (необязательно)</Label>
              <Input
                id="invite-name"
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="invite-contact">
                {channel === 'telegram' ? 'Telegram (@username)' : 'Эл. почта'}
              </Label>
              <Input
                id="invite-contact"
                value={contact}
                onChange={(e) => setContact(e.target.value)}
                placeholder={channel === 'telegram' ? '@username' : 'name@company.ru'}
              />
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={close}>
                Отмена
              </Button>
              <Button
                disabled={mutation.isPending || contact.trim() === ''}
                onClick={() =>
                  mutation.mutate({
                    role,
                    channel,
                    full_name: fullName.trim() || undefined,
                    email: channel === 'email' ? contact.trim() : undefined,
                    telegram: channel === 'telegram' ? contact.trim() : undefined,
                  })
                }
              >
                <Send aria-hidden="true" />
                {mutation.isPending ? 'Создаём…' : 'Создать приглашение'}
              </Button>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Страница
// ---------------------------------------------------------------------------

export function EmployerCompanyPage(): ReactNode {
  const queryClient = useQueryClient();
  const [editOpen, setEditOpen] = useState(false);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [revokeTarget, setRevokeTarget] = useState<MemberOut | null>(null);

  const companyQuery = useQuery({
    queryKey: ['employers', 'company'],
    queryFn: ({ signal }) => fetchMyCompany(signal),
  });
  const membersQuery = useQuery({
    queryKey: ['employers', 'members'],
    queryFn: ({ signal }) => fetchCompanyMembers(signal),
  });

  const kindMutation = useMutation({
    mutationFn: () => updateMyCompany({ kind: 'corporate' }),
    onSuccess: (updated) => {
      queryClient.setQueryData(['employers', 'company'], updated);
      notifySuccess('Профиль компании теперь корпоративный', 'Можно приглашать HR и менеджеров');
    },
    onError: (error) => notifyApiError(error),
  });

  const revokeMutation = useMutation({
    mutationFn: (memberId: string) => revokeMember(memberId),
    onSuccess: () => {
      notifySuccess('Доступ участника отозван');
      void queryClient.invalidateQueries({ queryKey: ['employers', 'members'] });
    },
    onError: (error) => notifyApiError(error, 'Не удалось отозвать доступ'),
  });

  if (companyQuery.isPending) return <LoadingState rows={6} />;
  if (companyQuery.isError) {
    return (
      <ErrorState error={companyQuery.error} onRetry={() => void companyQuery.refetch()} />
    );
  }
  const company = companyQuery.data;
  const members = membersQuery.data?.items ?? [];
  const canInvite = company.kind === 'corporate' && company.my_role !== 'hiring_manager';

  return (
    <>
      <PageHeader
        title="Компания"
        subtitle="Профиль, участники и доступы для HR и нанимающих менеджеров"
        meta={
          <StatusBadge status={company.kind === 'corporate' ? 'brand' : 'draft'}>
            {COMPANY_KIND_LABELS[company.kind]}
          </StatusBadge>
        }
        extra={
          <>
            <Button variant="outline" onClick={() => setEditOpen(true)}>
              <Building2 aria-hidden="true" />
              Редактировать
            </Button>
            <Button variant="brand" disabled={!canInvite} onClick={() => setInviteOpen(true)}>
              <Send aria-hidden="true" />
              Пригласить HR
            </Button>
          </>
        }
      />

      {company.kind === 'personal' ? (
        <section className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-primary-tint p-4">
          <p className="max-w-[560px] text-sm leading-relaxed text-primary-deep">
            У вас личный профиль работодателя: приглашения уходят от вашего имени. Чтобы позвать
            HR или нанимающих менеджеров и работать командой, сделайте профиль корпоративным.
          </p>
          <Button
            variant="brand"
            disabled={kindMutation.isPending}
            onClick={() => kindMutation.mutate()}
          >
            {kindMutation.isPending ? 'Сохраняем…' : 'Сделать корпоративной'}
          </Button>
        </section>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,420px)_1fr]">
        <SectionCard title="Профиль компании">
          <DL labelWidth={130}>
            <DLRow label="Название">{company.name}</DLRow>
            <DLRow label="ИНН">{formatText(company.inn)}</DLRow>
            <DLRow label="Сайт">{formatText(company.website)}</DLRow>
            <DLRow label="Эл. почта">{formatText(company.contact_email)}</DLRow>
            <DLRow label="Телефон">{formatText(company.contact_phone)}</DLRow>
            <DLRow label="Моя роль">{MEMBER_ROLE_LABELS[company.my_role]}</DLRow>
          </DL>
          {company.description ? (
            <p className="mt-3 border-t border-border pt-3 text-sm leading-relaxed text-muted-foreground">
              {company.description}
            </p>
          ) : null}
          <p className="mt-3 text-xs text-muted-foreground">
            Контакты шифруются и раскрываются кандидату только после принятого приглашения.
          </p>
        </SectionCard>

        <SectionCard
          title={`Участники (${members.length})`}
          extra={
            !canInvite && company.kind === 'corporate' ? (
              <span className="text-xs text-muted-foreground">
                Приглашает владелец или HR
              </span>
            ) : undefined
          }
        >
          {membersQuery.isPending ? (
            <LoadingState rows={3} card={false} />
          ) : membersQuery.isError ? (
            <ErrorState
              error={membersQuery.error}
              onRetry={() => void membersQuery.refetch()}
              compact
            />
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Участник</TableHead>
                    <TableHead>Роль</TableHead>
                    <TableHead>Статус</TableHead>
                    <TableHead>Приглашён</TableHead>
                    <TableHead aria-label="Действия" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {members.map((member) => (
                    <TableRow key={member.id}>
                      <TableCell>
                        <span className="flex flex-col">
                          <span className="font-medium">
                            {member.full_name_masked ?? EMPTY_MEMBER_NAME}
                          </span>
                          <span className="text-xs text-muted-foreground">
                            {member.email_masked ?? member.telegram_masked ?? '—'}
                            {!member.has_account ? ' · без входа на платформу' : ''}
                          </span>
                        </span>
                      </TableCell>
                      <TableCell>{MEMBER_ROLE_LABELS[member.role]}</TableCell>
                      <TableCell>
                        <StatusBadge
                          size="sm"
                          status={MEMBER_STATUS_STATUS[member.status]}
                          icon={member.status === 'active' ? Check : undefined}
                        >
                          {MEMBER_STATUS_LABELS[member.status]}
                        </StatusBadge>
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {formatDate(member.invited_at ?? member.accepted_at)}
                      </TableCell>
                      <TableCell className="text-right">
                        {member.role !== 'owner' && member.status !== 'revoked' ? (
                          <Button
                            variant="ghost"
                            size="icon"
                            aria-label={`Отозвать доступ: ${member.full_name_masked ?? member.id}`}
                            onClick={() => setRevokeTarget(member)}
                          >
                            <UserMinus aria-hidden="true" />
                          </Button>
                        ) : null}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </SectionCard>
      </div>

      <EditCompanyDialog company={company} open={editOpen} onClose={() => setEditOpen(false)} />
      <InviteMemberDialog open={inviteOpen} onClose={() => setInviteOpen(false)} />
      <ConfirmDialog
        open={revokeTarget !== null}
        onOpenChange={(next) => (next ? undefined : setRevokeTarget(null))}
        title="Отозвать доступ участника?"
        body={
          <p className="text-sm text-muted-foreground">
            Участник потеряет доступ к потребностям и воронке найма компании. Вернуть его можно
            повторным приглашением.
          </p>
        }
        confirmLabel="Отозвать"
        onConfirm={() => {
          if (revokeTarget) revokeMutation.mutate(revokeTarget.id);
          setRevokeTarget(null);
        }}
      />
    </>
  );
}

const EMPTY_MEMBER_NAME = 'Без имени';
