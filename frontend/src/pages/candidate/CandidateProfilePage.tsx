import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, Plus, Save, X } from 'lucide-react';
import { useState, type KeyboardEvent, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { fetchMyProfile, updateMyProfile } from '@/shared/api/endpoints/candidates';
import { listIndustries, listSpecializations } from '@/shared/api/endpoints/skills';
import { notifyApiError, notifySuccess } from '@/shared/api/feedback';
import { isApiError } from '@/shared/api/errors';
import type { CandidateProfile, CandidateProfileUpdate } from '@/shared/api/types';
import { cn } from '@/shared/lib/cn';
import { Badge } from '@/shared/ui/badge';
import { Button } from '@/shared/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/shared/ui/collapsible';
import { SectionCard } from '@/shared/ui/detail-blocks';
import { Input } from '@/shared/ui/input';
import { Label } from '@/shared/ui/label';
import { PageHeader } from '@/shared/ui/page-header';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/ui/select';
import { ErrorState, LoadingState } from '@/shared/ui/states';
import { StatusBadge } from '@/shared/ui/status-badge';
import { Switch } from '@/shared/ui/switch';
import { Textarea } from '@/shared/ui/textarea';

/**
 * «Профиль» кандидата — настоящая форма по GET|PUT /candidates/me (шаг 1 «Пути до категории»).
 * Данные из ФСП ID пока не тянутся (ограничение интеграции) — кандидат заполняет профиль сам.
 * Специализация обязательна для теста на грейд (без неё POST /assessment/sessions →
 * 409 profile_incomplete). PUT частичный: отправляются только изменённые поля.
 */

/** Редактор тегов заявленного стека: Enter/запятая добавляет, крестик удаляет. */
function StackTagsEditor({
  tags,
  onChange,
}: {
  tags: string[];
  onChange: (next: string[]) => void;
}): ReactNode {
  const [draft, setDraft] = useState('');

  const add = (): void => {
    const tag = draft.trim().toLowerCase().replace(/,+$/, '');
    setDraft('');
    if (!tag || tags.includes(tag)) return;
    onChange([...tags, tag]);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>): void => {
    if (event.key === 'Enter' || event.key === ',') {
      event.preventDefault();
      add();
    }
  };

  return (
    <div className="grid gap-2">
      <div className="flex gap-2">
        <Input
          id="profile-stack"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="python, postgresql, docker…"
          aria-label="Добавить технологию в заявленный стек"
        />
        <Button type="button" variant="outline" size="icon" aria-label="Добавить тег" onClick={add}>
          <Plus aria-hidden="true" />
        </Button>
      </div>
      {tags.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {tags.map((tag) => (
            <Badge key={tag} variant="secondary" className="gap-1 pr-1">
              {tag}
              <button
                type="button"
                className="rounded-sm p-0.5 hover:bg-muted"
                aria-label={`Убрать ${tag}`}
                onClick={() => onChange(tags.filter((t) => t !== tag))}
              >
                <X className="size-3" aria-hidden="true" />
              </button>
            </Badge>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function CandidateProfilePage(): ReactNode {
  const queryClient = useQueryClient();
  // Черновик формы: только изменённые поля (семантика частичного PUT backend'а)
  const [form, setForm] = useState<CandidateProfileUpdate>({});
  const [contactsOpen, setContactsOpen] = useState(false);

  const profileQuery = useQuery({
    queryKey: ['candidates', 'me'],
    queryFn: ({ signal }) => fetchMyProfile(signal),
    retry: false,
  });
  const industriesQuery = useQuery({
    queryKey: ['skills', 'industries'],
    queryFn: ({ signal }) => listIndustries(signal),
  });
  const specializationsQuery = useQuery({
    queryKey: ['skills', 'specializations'],
    queryFn: ({ signal }) => listSpecializations(signal),
  });

  const mutation = useMutation({
    mutationFn: (body: CandidateProfileUpdate) => updateMyProfile(body),
    onSuccess: (updated) => {
      queryClient.setQueryData(['candidates', 'me'], updated);
      setForm({});
      notifySuccess('Профиль сохранён', 'Шаг «Профиль» на пути до категории выполнен');
    },
    onError: (error) => notifyApiError(error, 'Не удалось сохранить профиль'),
  });

  // 404 — профиль ещё не создан (старый контракт): форма с пустыми значениями, PUT создаст
  const softMissing =
    profileQuery.isError && isApiError(profileQuery.error) && profileQuery.error.status === 404;
  if (profileQuery.isPending) return <LoadingState rows={8} />;
  if (profileQuery.isError && !softMissing) {
    return <ErrorState error={profileQuery.error} onRetry={() => void profileQuery.refetch()} />;
  }
  const profile: CandidateProfile | null = profileQuery.data ?? null;

  const value = <K extends keyof CandidateProfileUpdate>(key: K, fallback: string | null): string =>
    (form[key] as string | null | undefined) ?? fallback ?? '';
  const setText = (key: keyof CandidateProfileUpdate) => (v: string) =>
    setForm((prev) => ({ ...prev, [key]: v === '' ? null : v }));

  const stackTags = form.stack_tags_declared ?? profile?.stack_tags_declared ?? [];
  const searchable = form.privacy?.searchable ?? profile?.privacy.searchable ?? true;
  const specialization = value('specialization', profile?.specialization ?? null);
  const dirty = Object.keys(form).length > 0;

  const save = (): void => {
    if (!dirty || mutation.isPending) return;
    mutation.mutate(form);
  };

  return (
    <>
      <PageHeader
        title="Профиль"
        subtitle="Анкета кандидата: данные из ФСП ID пока не подтягиваются — заполните вручную"
        meta={
          profile ? <StatusBadge status="brand">Псевдоним {profile.pseudonym}</StatusBadge> : null
        }
        extra={
          <Button variant="brand" disabled={!dirty || mutation.isPending} onClick={save}>
            <Save aria-hidden="true" />
            {mutation.isPending ? 'Сохраняем…' : 'Сохранить'}
          </Button>
        }
      />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="grid content-start gap-4">
          <SectionCard title="Основное">
            <div className="grid gap-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="grid gap-1.5">
                  <Label htmlFor="profile-city">Город</Label>
                  <Input
                    id="profile-city"
                    value={value('city', profile?.city ?? null)}
                    onChange={(e) => setText('city')(e.target.value)}
                    placeholder="Москва"
                  />
                </div>
                <div className="grid gap-1.5">
                  <Label>Отрасль</Label>
                  <Select
                    value={value('industry_code', profile?.industry_code ?? null) || undefined}
                    onValueChange={setText('industry_code')}
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
                <Label>
                  Специализация <span className="text-status-danger">*</span>
                </Label>
                <Select
                  value={specialization || undefined}
                  onValueChange={setText('specialization')}
                >
                  <SelectTrigger aria-label="Специализация">
                    <SelectValue placeholder="Выберите специализацию" />
                  </SelectTrigger>
                  <SelectContent>
                    {(specializationsQuery.data ?? []).map((spec) => (
                      <SelectItem key={spec.code} value={spec.code} disabled={spec.status !== 'active'}>
                        {spec.title}
                        {spec.status !== 'active' ? ' — скоро' : ''}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">
                  Обязательна для теста на грейд: по ней собираются задания и граф навыков.
                </p>
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="profile-stack">Заявленный стек</Label>
                <StackTagsEditor
                  tags={stackTags}
                  onChange={(next) => setForm((prev) => ({ ...prev, stack_tags_declared: next }))}
                />
                <p className="text-xs text-muted-foreground">
                  Заявлено вами — в ранжировании не участвует, стек подтверждает тест.
                </p>
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="profile-about">О себе</Label>
                <Textarea
                  id="profile-about"
                  rows={4}
                  maxLength={2000}
                  value={value('about', profile?.about ?? null)}
                  onChange={(e) => setText('about')(e.target.value)}
                  placeholder="Чем занимались, что делали сами, что интересно развивать"
                />
                <p className="text-xs text-muted-foreground">
                  Не указывайте контакты в тексте — санитайзер заменит их плейсхолдерами.
                </p>
              </div>
            </div>
          </SectionCard>

          <SectionCard title="Контакты">
            <Collapsible open={contactsOpen} onOpenChange={setContactsOpen}>
              <CollapsibleTrigger asChild>
                <Button variant="ghost" className="-ml-2 gap-1.5">
                  <ChevronDown
                    className={cn('size-4 transition-transform', contactsOpen && 'rotate-180')}
                    aria-hidden="true"
                  />
                  {contactsOpen ? 'Свернуть' : 'Указать контакты'}
                </Button>
              </CollapsibleTrigger>
              <p className="mt-1 text-xs text-muted-foreground">
                По желанию. Контакты шифруются и раскрываются работодателю только после принятого
                вами приглашения.
              </p>
              <CollapsibleContent>
                <div className="mt-3 grid gap-4">
                  <div className="grid gap-1.5">
                    <Label htmlFor="profile-name">ФИО</Label>
                    <Input
                      id="profile-name"
                      value={value('full_name', profile?.full_name ?? null)}
                      onChange={(e) => setText('full_name')(e.target.value)}
                    />
                  </div>
                  <div className="grid gap-4 sm:grid-cols-2">
                    <div className="grid gap-1.5">
                      <Label htmlFor="profile-phone">Телефон</Label>
                      <Input
                        id="profile-phone"
                        value={value('phone', profile?.phone ?? null)}
                        onChange={(e) => setText('phone')(e.target.value)}
                        placeholder="+7 900 000-00-00"
                      />
                    </div>
                    <div className="grid gap-1.5">
                      <Label htmlFor="profile-telegram">Telegram</Label>
                      <Input
                        id="profile-telegram"
                        value={value('telegram', profile?.telegram ?? null)}
                        onChange={(e) => setText('telegram')(e.target.value)}
                        placeholder="@username"
                      />
                    </div>
                  </div>
                </div>
              </CollapsibleContent>
            </Collapsible>
          </SectionCard>
        </div>

        <div className="grid content-start gap-4">
          <SectionCard title="Видимость">
            <div className="flex items-start justify-between gap-3">
              <div>
                <Label htmlFor="profile-searchable" className="text-sm font-medium">
                  Показывать в банке кандидатов
                </Label>
                <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                  Работодатели увидят профиль под псевдонимом и только после присвоения категории.
                </p>
              </div>
              <Switch
                id="profile-searchable"
                checked={searchable}
                onCheckedChange={(checked) =>
                  setForm((prev) => ({
                    ...prev,
                    privacy: {
                      searchable: checked,
                      show_city: profile?.privacy.show_city ?? true,
                      show_fsp: profile?.privacy.show_fsp ?? true,
                      show_accomplishments: profile?.privacy.show_accomplishments ?? true,
                      hidden_company_ids: profile?.privacy.hidden_company_ids ?? [],
                    },
                  }))
                }
              />
            </div>
          </SectionCard>
          <SectionCard title="Дальше по пути">
            <p className="text-sm leading-relaxed text-muted-foreground">
              После сохранения профиля — короткий опрос (по желанию) и тест на грейд: он
              подтверждает узлы графа и присваивает категорию.
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button variant="outline" size="sm" asChild>
                <Link to="/candidate/survey">Опрос и свершения</Link>
              </Button>
              <Button variant="brand" size="sm" asChild>
                <Link to="/candidate/test">К тесту</Link>
              </Button>
            </div>
          </SectionCard>
        </div>
      </div>
    </>
  );
}
