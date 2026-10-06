# Библиотеки и компоненты

Статус: draft (11 октября, день сдачи). Версии — фактически установленные
(`importlib.metadata` из `backend/.venv`, `node_modules/*/package.json` фронтенда);
диапазоны зависимостей — `backend/pyproject.toml` и `frontend/package.json`.
Лицензия проекта — MIT; все зависимости — пермиссивные (MIT / Apache-2.0 / BSD / ISC /
OFL / MPL-2.0), копилефта нет.

## 1. Backend (Python 3.12)

| Пакет | Версия | Лицензия | Зачем |
|---|---|---|---|
| fastapi | 0.142.2 | MIT | веб-каркас API: роутеры, DI, автогенерация OpenAPI |
| uvicorn[standard] | 0.54.0 | BSD-3-Clause | ASGI-сервер приложения |
| SQLAlchemy[asyncio] | 2.1.3 | MIT | ORM и ядро запросов (async) |
| asyncpg | 0.31.0 | Apache-2.0 | асинхронный драйвер PostgreSQL |
| alembic | 1.20.0 | MIT | миграции схемы БД (пишутся вручную, `alembic check`) |
| pydantic | 2.13.5 | MIT | схемы запросов/ответов, строгая валидация |
| pydantic-settings | 2.15.0 | MIT | конфигурация из env (UPPER_CASE, `.env`) |
| PyJWT[crypto] | 2.15.1 | MIT | проверка JWT Keycloak по JWKS |
| cryptography | 50.0.2 | Apache-2.0 / BSD-3-Clause | Fernet-шифрование ПДн, HMAC blind-index |
| httpx | 0.28.1 | BSD-3-Clause | HTTP-клиент (LLM, Keycloak, стаб реестра ФСП) и TestClient |
| PyYAML | 6.0.3 | MIT | YAML графов навыков, профилей грейдов, шаблонов |
| gigachat | 0.2.3 | MIT | SDK основного LLM-провайдера (GigaChat) |
| yandex-cloud-ml-sdk | 0.19.1 | Apache-2.0 | резервный LLM-провайдер (YandexGPT) |
| pypdf | 6.19.0 | BSD-3-Clause | извлечение текста из PDF-резюме |
| orjson | 3.12.0 | MPL-2.0 + (Apache-2.0/MIT) | быстрая JSON-сериализация ответов |
| python-multipart | 0.0.32 | Apache-2.0 | приём multipart-загрузок (PDF-резюме) |
| aiogram | 3.31.0 | MIT | Telegram-бот (long polling) и проверка initData мини-аппа |

Dev-зависимости: pytest 9.1.1 (MIT), pytest-asyncio 1.4.0 (Apache-2.0), ruff 0.16.10 (MIT).
Экстра `validate` (только для `app/tools/validate`, в образ backend не ставится):
numpy, pandas, scikit-learn (BSD-3-Clause), matplotlib (PSF/BSD-совместимая).

## 2. Frontend (Node 22)

| Пакет | Версия | Лицензия | Зачем |
|---|---|---|---|
| react / react-dom | 18.3.1 | MIT | UI-библиотека SPA |
| typescript | 5.8.3 | Apache-2.0 | статическая типизация (`tsc --noEmit` в CI) |
| vite / @vitejs/plugin-react | 6.4.3 / 4.7.0 | MIT | сборка и dev-сервер с прокси `/api` |
| tailwindcss (+ @tailwindcss/vite) | 4.3.3 | MIT | утилитарные стили, токены палитры ФСП |
| @radix-ui/react-* (17 пакетов) | 1.x–2.x | MIT | безбарьерные примитивы shadcn/ui (диалоги, меню, табы…) |
| @tanstack/react-query | 5.103.0 | MIT | серверное состояние, кэш и refetch при фокусе |
| zustand | 5.0.15 | MIT | лёгкое клиентское состояние |
| react-router-dom | 6.30.6 | MIT | маршрутизация кабинетов |
| react-hook-form + @hookform/resolvers | 7.89.0 | MIT | формы с валидацией |
| zod | 3.25.76 | MIT | схемы валидации форм и ответов API |
| keycloak-js | 26.2.4 | Apache-2.0 | OIDC-вход (PKCE) в realm `fsp` |
| echarts + echarts-for-react | 5.6.0 / 3.0.6 | Apache-2.0 / MIT | граф навыков и диаграммы |
| lucide-react | 1.48.0 | ISC | иконки |
| motion | 12.43.0 | MIT | анимации интерфейса |
| sonner | 2.0.8 | MIT | тосты-уведомления |
| dayjs | 1.11.23 | MIT | работа с датами (свежесть подтверждений) |
| @fontsource-variable/jetbrains-mono | 5.3.0 | OFL-1.1 (шрифт) | фирменный шрифт ФСП, кириллица в бандле |
| clsx / tailwind-merge / class-variance-authority | 2.1.1 / 3.7.0 / 0.7.1 | MIT / MIT / Apache-2.0 | служебные утилиты классов shadcn/ui |

## 3. Инфраструктура

| Компонент | Версия (образ) | Лицензия | Зачем |
|---|---|---|---|
| PostgreSQL | `postgres:16-alpine` | PostgreSQL License | основная БД |
| Keycloak | `quay.io/keycloak/keycloak:26.0` | Apache-2.0 | аутентификация, realm `fsp`, SMTP-письма |
| Mailpit | `axllent/mailpit:v1.31` | MIT | «почта стенда»: ловит письма подтверждения/сброса |
| Caddy | `caddy:2` | Apache-2.0 | TLS-терминация и reverse-proxy на VPS (Let's Encrypt) |
| nginx (в образе фронта) | stable-alpine | BSD-2-Clause | раздача SPA и прокси `/api` внутри сети |
| Docker Compose | v2 | Apache-2.0 | оркестрация dev/full/prod профилей |
| GitHub Actions + gitleaks | облако / v8 | — / MIT | CI: ruff, pytest, tsc, build, скан секретов |
| k6 | последний образ | AGPL-3.0 (только инструмент) | нагрузочный прогон; в состав решения не входит |

## 4. Заимствованный код

Каркас проекта — репозиторий «Альма» (ЛЦТ 2026): собственная разработка участников
команды, заимствование согласовано правилами хакатона. Взято: примитивы `app/core`
(конфиг, ошибки, безопасность, ПДн-шифрование, аудит, пагинация), UI-кит shadcn/ui
с токенами, compose-профили и CI; переписано под домен ФСП (`crm` → `fsp`): модель данных,
все модули API, граф навыков, тестирование и подбор — новые. Модель компетенций —
методологическая основа с атрибуцией одной строкой ([`skills-model.md`](skills-model.md) §1);
формулы, графы и данные — собственные.

## 5. Шрифты и бренд

Шрифт JetBrains Mono Variable — лицензия SIL OFL 1.1, поставляется npm-пакетом
`@fontsource-variable/jetbrains-mono` (кириллица в бандле). Палитра — официальный
брендбук ФСП (fsp-russia.ru/press/brandbook): #402FFF / #EC1D35 / #1B1C21 / #8F94E3.
Логотипы ФСП — `frontend/public/brand/`; логотип ЛЦТ — только в презентации,
по правилам организатора.
