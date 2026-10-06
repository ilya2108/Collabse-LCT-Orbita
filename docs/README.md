# Документация «ФСП.Орбиты»

Сопроводительная документация решения спец. трека ФСП на хакатоне ЛЦТ 2026. Markdown-файлы
этой папки собираются `pandoc` в один PDF/DOCX по спискам из [`manifest.yaml`](manifest.yaml)
(два списка сборки: для команды из трёх и из двух человек). Требования ТЗ пересказываются
своими словами — текст ТЗ конфиденциален и в репозиторий не входит (`docs/source/` в `.gitignore`).

Состояние на 11 октября 2026 (день сдачи): содержательно заполнены архитектура (прод-топология),
тестирование, API (`06-api.md` + `openapi.json`), библиотеки, запуск/эксплуатация, трассировка
требований, `skills-model.md`, `demo-script.md`, ADR. Журнал работ — [`PROGRESS.md`](PROGRESS.md).
Живой стенд: <https://orbita.185.239.51.92.nip.io>.

## Карта «пункт 3.4 ТЗ → файл»

Статусы: `skeleton` — заголовки и план содержания; `draft` — черновик, нужна вычитка;
`ready` — вычитано; `generated` — собирается скриптом, руками не правится.

| Содержание (п. 3.4 ТЗ, своими словами) | Файл (трое) | Файл (двое) | Статус | Кто |
|---|---|---|---|---|
| Краткое описание подхода для промежуточной сдачи (2 стр.) | [`00-approach.md`](00-approach.md) | то же | skeleton | C (7 окт) |
| Функциональная и компонентная архитектура | [`01-architecture.md`](01-architecture.md) | то же (модель — разделом) | draft | C + A (8 окт) |
| Модель компетенций: основа, графы, формулы, грейды | [`skills-model.md`](skills-model.md) | раздел в `01-architecture.md` | draft | C |
| Механика тестирования и устойчивость к утечке заданий | [`02-testing.md`](02-testing.md) | то же | draft | A + C (8 окт) |
| Механика подбора и категоризация | [`03-matching.md`](03-matching.md) | то же | skeleton | A + C (9 окт) |
| Процедура валидации | [`04-validation.md`](04-validation.md) | то же | skeleton | C (10 окт) |
| Результаты валидации | [`validation-report.md`](validation-report.md) | то же | skeleton | C (v1 — 9 окт, v2 — 10 окт) |
| Интеграция с реестром ФСП и ФСП ID | [`05-fsp-integration.md`](05-fsp-integration.md) | то же | skeleton | C + A (9 окт) |
| Описание API | [`06-api.md`](06-api.md) + `openapi.json` | `06-run-and-api.md` + `openapi.json` | draft | A (10 окт) |
| Библиотеки и компоненты с лицензиями | [`07-libraries.md`](07-libraries.md) | раздел в `06-run-and-api.md` | draft | A (10 окт, скриптом) |
| Сборка, развёртывание, локальный запуск | [`08-run.md`](08-run.md) | `06-run-and-api.md` | draft | A (черновик 6 окт, проверка 10 окт) |
| Безопасность и 152-ФЗ | [`security-152fz.md`](security-152fz.md) | то же | skeleton | A |
| Концепции за пределами MVP | [`09-beyond-mvp.md`](09-beyond-mvp.md) | то же (roadmap — разделом) | skeleton | C (9 окт) |
| Дорожная карта | [`roadmap.md`](roadmap.md) | раздел в `09-beyond-mvp.md` | skeleton | все |
| Трассировка «требование → где реализовано» | [`compliance.md`](compliance.md) | то же | draft | A (10 окт); чек-лист — `drafts/compliance-checklist.md` |
| Сценарий демонстрации («путь жюри за 5 минут») | [`demo-script.md`](demo-script.md) | то же | draft | C (вычитка 10 окт) |
| Задания по описаниям вакансий | `demo-vacancies/report.md` | то же | generated (`make demo-needs`, 8 окт) | C |
| Спецификация шаблона задания | `templates-spec.md` | то же | — (зона templates) | C |

## Служебные документы (в PDF не входят)

| Файл | Назначение |
|---|---|
| [`PROGRESS.md`](PROGRESS.md) | Журнал по дням: что сделано, что перенесено, решения синков |
| [`manifest.yaml`](manifest.yaml) | Списки файлов для сборки PDF (трое / двое), метаданные pandoc |
| [`adr/`](adr/) | Архитектурные решения: [001 провайдер LLM](adr/001-llm-provider.md), [002 формулы грейда и затухания](adr/002-grade-and-decay-formulas.md), [003 compose вместо k8s](adr/003-compose-instead-of-k8s.md) |
| [`drafts/`](drafts/) | Черновики наружу (отправляет человек): [письмо автору модели](drafts/letter-to-author.md), [вопросы экспертам ФСП](drafts/questions-to-fsp.md), [приглашение в живую панель](drafts/live-panel-invite.md), [чек-лист требований](drafts/compliance-checklist.md); рабочие разборы: [итоги Q&A организаторов 06.10](drafts/qa-implications-2026-10-08.md) |
| `source/` | Материалы организаторов (ТЗ, регламент, шаблон презентации) — в git не входят |
| `validation/` | PNG-графики отчёта валидации (`raw/` в `.gitignore`) |
| `presentation/` | Презентация (pptx/pdf) — появится 10 октября |

## Как собрать PDF

```bash
# из корня репозитория; pandoc и шрифт Montserrat должны быть установлены
make docs            # цель появится в Makefile (зона infra); под капотом:
pandoc --defaults docs/manifest.yaml   # либо явный список файлов из manifest.yaml
```

Правила для авторов: заголовок первого уровня — один на файл; ссылки между файлами —
относительные; схемы — mermaid в тексте (pandoc с фильтром) или PNG в `docs/validation/`;
атрибуция модели компетенций — одной строкой там, где план требует (`skills-model.md`,
`02-testing.md` §1, слайд 3); цифры валидации — только из `validation-report.md`.
