# 08. Запуск и эксплуатация

Статус: draft (11 октября, день сдачи). Полный мануал: локальная разработка (§2–7),
прод-деплой на VPS (§8), эксплуатация (§9), траблшутинг (§10), нагрузочный прогон (§11).
Живой стенд: <https://orbita.185.239.51.92.nip.io> (Swagger — `/api/v1/docs`,
Keycloak — `/auth`, «Почта стенда» — `/mail/`).

## 1. Требования

| Что | Версия | Зачем |
|---|---|---|
| Docker Desktop / Engine + Compose v2 | 24+ / 2.20+ | `make dev`: PostgreSQL, Keycloak, Mailpit, backend |
| GNU make | любая | цели верхнего уровня (`make help`) |
| Python | 3.12 | backend на хосте, `make test`, `make lint` |
| Node.js | 22 | frontend на хосте (`npm run dev`), `tsc` |
| openssl | любая | генерация ключей в `make dev-env` |

Ключи LLM не нужны: по умолчанию `LLM_PROVIDER=stub` (фикстуры и эвристики), см. §6.
Память стека ≈ 1,5 ГБ (Keycloak ~600 МБ).

## 2. Быстрый старт

```sh
make dev-env   # deploy/compose/.env: ключи ПДн (Fernet, HMAC), внутренний токен, секрет клиента
make dev       # postgres + keycloak (realm fsp) + mailpit + migrate + backend (hot-reload)
cd frontend && npm ci && npm run dev   # http://localhost:5173, прокси /api → :8000
```

Первый запуск скачивает образы (postgres, keycloak, mailpit, базовые образы backend — около 1 ГБ).
Keycloak поднимается 1–2 минуты (импорт realm при первом старте); `backend` стартует после
`migrate` (alembic upgrade head) и готовности Keycloak. Проверка:

| Сервис | Адрес | Проверка |
|---|---|---|
| API | http://localhost:8000/api/v1/docs | `curl localhost:8000/api/v1/health/ready` → `{"status":"ok"}` |
| Keycloak | http://localhost:8080 | `admin` / `KC_ADMIN_PASSWORD` из `deploy/compose/.env`; realm `fsp` |
| Почта стенда (Mailpit) | http://localhost:8025 | письма подтверждения email и сброса пароля |
| Фронт (dev) | http://localhost:5173 | vite на хосте |
| Фронт (контейнер) | http://localhost:3000 | `make dev-full` (профиль `full`, nginx) |

Все порты dev-стенда публикуются только на `127.0.0.1`: консоль Keycloak, Mailpit без пароля
и PostgreSQL не видны соседям по сети. Пароли консоли Keycloak и демо-администратора
генерируются `make dev-env` (значения — в `deploy/compose/.env`); realm требует пароли
от 10 символов (`passwordPolicy`).

Остановить: `make dev-down` (тома сохраняются), снести с данными: `make clean`
(`deploy/compose/.env` сохраняется — ключ шифрования ПДн стабилен, иначе данные в БД нечитаемы).

### Backend на хосте (без контейнера backend)

```sh
cp .env.example backend/.env      # PII_FERNET_KEYS и PII_HMAC_KEY — взять из deploy/compose/.env
make dev-infra                    # только postgres + keycloak + mailpit
cd backend && python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/alembic upgrade head && .venv/bin/uvicorn app.main:app --reload --port 8000
```

## 3. Миграции и сид

- `make migrate` — `alembic upgrade head` в контейнере backend (образ `fsp-backend:dev`).
  Сервис `migrate` выполняется автоматически при каждом `make dev`; backend ждёт его завершения.
- `make seed` — идемпотентный сид (`python -m app.tools.seed`): справочники, демо-пользователи,
  компании. Повторный запуск ничего не дублирует.
- `make demo` — подготовка демо-стенда: пока только сид; пул заданий и сценарий жюри — TODO.
- Проверка «схема = head»: `python -m app.tools.check_migrations` (exit 1, пока ревизия отстаёт).

## 4. Регистрация и роли

Keycloak realm `fsp`: самостоятельная регистрация включена, email подтверждается письмом
(в dev письмо падает в Mailpit — http://localhost:8025, кнопка «Подтвердить»), вход по email
или логину, сброс пароля — также через письмо. Роль (`candidate` / `employer`) хранится в БД:
при первом входе ролей нет, фронт показывает экран выбора роли (`POST /auth/role`).
Роль `admin` назначается только в Keycloak и приходит из токена.

Демо-аккаунты: `fsp-admin` / `DEMO_ADMIN_PASSWORD` (из `deploy/compose/.env`, роль `admin`);
демо-кандидаты и работодатели создаются сидом (`make seed`), их логины и пароли стенда —
в материалах сдачи (в репозиторий не кладутся). Пересев демо-данных без пересоздания стенда —
`POST /api/v1/internal/demo/reset` (по `X-Internal-Token`). Путь жюри кликами —
[`demo-script.md`](demo-script.md).

## 5. Почта стенда

Mailpit принимает всю почту Keycloak (SMTP `mailpit:1025`, from `noreply@fsp.local`) и
показывает её в UI на :8025 — реальный SMTP не нужен. На публичном стенде Mailpit открыт
как «Почта стенда» на `https://<домен>/mail/` за basic-auth Caddy (пользователь `mail`),
письма старше 24 ч удаляются (`MP_MAX_AGE=24h`) — см. §8.

## 6. Режимы LLM

| `LLM_PROVIDER` | Поведение | Когда |
|---|---|---|
| `stub` (по умолчанию) | фикстуры и эвристики, токены не расходуются | CI, локальная разработка, нагрузочный прогон |
| `gigachat` | основной провайдер (`GIGACHAT_CREDENTIALS`, scope `GIGACHAT_API_PERS`) | стенд, генерация пула |
| `yandex` | резерв (`YC_FOLDER_ID`, `YC_API_KEY`) | при недоступности GigaChat |
| `replay` | ответы из репозитория | демо без сети |
| `rules` | словарь ключевых слов | разбор резюме без LLM |

Переключение — переменные в `deploy/compose/.env` (контейнер) или `backend/.env` (хост),
затем `make dev` (пересоздаст backend). Smoke-тест строгого JSON: `make llm-smoke`.
Суточный лимит токенов — `LLM_DAILY_TOKEN_CAP`, параллелизм — `LLM_MAX_CONCURRENCY`.

## 7. Качество и CI

`make lint` (ruff check + format), `make test` (pytest backend + tsc frontend). CI
(`.github/workflows/ci.yml`): backend ruff/pytest, frontend tsc/build, gitleaks (вся история),
`docker compose config` + проверка realm, сборка образов backend и frontend.
Секреты в репозиторий не попадают: только `*.env.example` с плейсхолдерами `__GEN_*__`.

## 8. Прод-стенд (VPS, Caddy)

Прод-профиль — `deploy/compose/docker-compose.prod.yml`: Caddy (80/443, автоматический
HTTPS Let's Encrypt) → nginx фронта (SPA + прокси `/api`) → backend; Keycloak в прод-режиме
(`start`, `KC_HTTP_RELATIVE_PATH=/auth`) на `https://<домен>/auth`, «Почта стенда» (Mailpit)
на `https://<домен>/mail/` за basic-auth. Наружу открыты только 80/443; у всех сервисов
`restart: unless-stopped` — стенд переживает перезагрузку VPS. `vm-deploy.sh` по-прежнему
отказывается выкатывать dev-compose на VPS (известные пароли, Keycloak start-dev);
контейнерный nginx фронта не проксирует `/api/v1/internal/*` и
`/api/v1/assessment/internal/*` — джобы вызывают backend напрямую по внутренней сети.

### Требования к VPS

- 2 vCPU, 4 ГБ RAM (Keycloak ~600 МБ + сборка фронта `npm run build`), 20 ГБ диска;
- Ubuntu 22.04+ (или аналог) с docker + compose v2, rsync; вход по ssh-ключу;
- открытые порты 80 и 443 (файрвол/облачная security group).

### Порядок деплоя

1. **DNS.** A-запись домена на IP VPS — или без своего домена `orbita.<ip>.nip.io`
   (nip.io резолвит сам). Внимание: nip.io/sslip.io делят лимиты Let's Encrypt со всеми
   пользователями — если выпуск сертификата упирается в rate-limit (50 сертификатов на
   зарегистрированный домен в неделю, 5 дубликатов на одно имя), попробуйте sslip.io или
   свой домен. Сертификаты лежат в томе `caddy_data` и при повторных деплоях не
   перевыпускаются.
2. **Первый прогон** (создаст каталог, сгенерирует секреты в `.env`; `compose up` упадёт
   с понятной ошибкой «заполните PUBLIC_DOMAIN» — это ожидаемо):

   ```sh
   VM_HOST=root@<ip> PUBLIC_DOMAIN=orbita.<ip>.nip.io make prod-deploy
   ```

3. **Заполнить `.env` на VPS** — `ssh root@<ip> nano /opt/fsp-orbita/deploy/compose/.env`
   (rsync этот файл не трогает, повторные деплои его не перетирают):
   - `PUBLIC_DOMAIN=` — домен из шага 1;
   - `ACME_EMAIL=` — почта для Let's Encrypt;
   - `MAILPIT_BASIC_AUTH=` — bcrypt-хэш пароля «Почты стенда» (пользователь `mail`),
     генерация сразу с удвоением `$` (его требует интерполяция docker compose):

     ```sh
     docker run --rm caddy:2 caddy hash-password --plaintext 'ваш-пароль' | sed 's/\$/$$/g'
     ```

   - GigaChat: `LLM_PROVIDER=gigachat`, `GIGACHAT_CREDENTIALS=<Authorization key
     с developers.sber.ru>`, scope `GIGACHAT_API_PERS` (персональный) уже стоит по умолчанию;
     плюс сертификаты НУЦ Минцифры — обязательный подшаг, см. «Сертификаты НУЦ» ниже;
   - Телеграм-бот (после BotFather, шаг 7): `TELEGRAM_BOT_MODE=polling`,
     `TELEGRAM_BOT_TOKEN=`, `TELEGRAM_BOT_USERNAME=`; если хостер фильтрует исходящий
     трафик к `api.telegram.org` (наш случай) — оставить `TELEGRAM_BOT_MODE=stub`, см. §10;
   - остальные секреты (`KC_ADMIN_PASSWORD`, `PII_*`, `INTERNAL_API_TOKEN`,
     `BACKEND_ADMIN_CLIENT_SECRET`, `DEMO_ADMIN_PASSWORD`) уже сгенерированы на шаге 2.
4. **Деплой**: снова `VM_HOST=root@<ip> PUBLIC_DOMAIN=<домен> make prod-deploy` —
   rsync → `docker compose up -d --build` (миграции выполняет одноразовый сервис
   `migrate`) → smoke-проверка `/healthz`, `/api/v1/health/ready` и realm `fsp`.
   Повторный smoke в любой момент: `make prod-smoke` (те же переменные).
5. **Сид и демо-данные** (идемпотентно):

   ```sh
   ssh root@<ip> "cd /opt/fsp-orbita && docker compose -f deploy/compose/docker-compose.prod.yml \
       run --rm backend python -m app.tools.seed"
   ```

6. **Smoke LLM** (строгий JSON через GigaChat):

   ```sh
   ssh root@<ip> "cd /opt/fsp-orbita && docker compose -f deploy/compose/docker-compose.prod.yml \
       run --rm backend python -m app.tools.llm_smoke"
   ```

7. **Телеграм-бот**: у @BotFather создать бота (наш — `@OrbitaFspBot`), токен и username
   вписать в `.env` (шаг 3), затем `docker compose -f deploy/compose/docker-compose.prod.yml
   up -d bot` (или полный `make prod-deploy`). Если VPS не достукивается до
   `api.telegram.org` (фильтрация у хостера) — бот остаётся в `TELEGRAM_BOT_MODE=stub`:
   исходящие пишутся в журнал `tg_outbox` (смотреть — `GET /api/v1/internal/tgbot/outbox`
   по `X-Internal-Token`), а живой поллинг на время демо запускается с машины демонстратора
   через ssh-туннель к Postgres стенда (см. §10).

### Сертификаты НУЦ Минцифры (для GigaChat)

API GigaChat работает по TLS с сертификатами Национального удостоверяющего центра —
без доверия к ним httpx обрывает соединение. Пошагово:

1. Скачать с портала Госуслуг (gu-st.ru) корневой и выпускающий сертификаты
   «Russian Trusted Root CA» и «Russian Trusted Sub CA».
2. **Важно:** файлы с gu-st.ru — PEM с переводами строк CRLF; нормализовать каждый
   прогоном через openssl (заодно проверяет парсинг):

   ```sh
   openssl x509 -in russian_trusted_root_ca.cer -out root.pem
   openssl x509 -in russian_trusted_sub_ca.cer  -out sub.pem
   ```

3. Склеить в бандл и положить на VPS:

   ```sh
   cat root.pem sub.pem > russian_trusted_bundle.pem
   scp russian_trusted_bundle.pem root@<ip>:/opt/certs/russian_trusted_bundle.pem
   ```

4. В `.env` на VPS указать `GIGACHAT_CA_BUNDLE=/opt/certs/russian_trusted_bundle.pem`
   (каталог `/opt/certs` монтируется в контейнер backend) и передеплоить. Проверка —
   `make llm-smoke` / шаг 6 выше. Альтернатива `GIGACHAT_VERIFY_SSL_CERTS=false`
   осознанно не используется.

Адреса стенда: приложение `https://<домен>`, API-доки `https://<домен>/api/v1/docs`,
консоль Keycloak `https://<домен>/auth/admin` (`admin` / `KC_ADMIN_PASSWORD` из `.env`
на VPS), почта `https://<домен>/mail/` (`mail` / пароль из шага 3). Демо-администратор
realm — `fsp-admin` / `DEMO_ADMIN_PASSWORD`.

Важно: realm `fsp` импортируется только при **первом** старте Keycloak (пустая БД).
Смена `PUBLIC_DOMAIN` после первого запуска требует либо обновить redirect URI клиента
`fsp-frontend` в консоли Keycloak, либо пересоздать стенд
(`docker compose -f ... down -v` — удалит и данные приложения!).

## 9. Эксплуатация

- **Логи**: `ssh root@<ip> "cd /opt/fsp-orbita && docker compose -f
  deploy/compose/docker-compose.prod.yml logs -f backend"` (аналогично `caddy`, `keycloak`,
  `frontend`, `scheduler`, `bot`). В логах нет ПДн (шифрование + санитайзер), request id
  из заголовка `X-Request-Id` ищется прямо в логе backend.
- **Рестарт**: `docker compose -f deploy/compose/docker-compose.prod.yml restart backend`
  (или `up -d` после правки `.env`). У всех сервисов `restart: unless-stopped` — стенд
  переживает перезагрузку VPS. По правилам хакатона после дедлайна **рестартовать стенд
  можно, менять код и данные — нельзя**: допустимы только `restart`/`up -d` без изменения
  образов и `.env`; пересборка (`--build`), новые коммиты и `seed`/`demo/reset` после
  заморозки не выполняются.
- **Ротация ключей ПДн**: добавить новый Fernet-ключ первым в `PII_FERNET_KEYS`
  (список через запятую, старые остаются для чтения), передеплоить, затем
  `docker compose ... run --rm backend python -m app.tools.rekey` — перешифрует колонки
  новым ключом; после прогона старый ключ можно убрать.
- **Диск и почта**: письма Mailpit старше 24 ч удаляются сами (`MP_MAX_AGE=24h`);
  сертификаты Let's Encrypt живут в томе `caddy_data` и не перевыпускаются при деплоях.

## 10. Траблшутинг

| Симптом | Причина | Что делать |
|---|---|---|
| Порт 80 отвечает чужим 404 (не Caddy), хотя контейнеры живы | на VPS остался k3s/иной ingress: его CNI-правила iptables перехватывают 80/443 раньше Docker | проверить `iptables-save \| grep -i cni` и `systemctl list-units \| grep k3s`; остановить/удалить k3s (или его ingress), перезапустить docker; после очистки Caddy забирает порты |
| Let's Encrypt не выдаёт сертификат: rate limit | nip.io/sslip.io делят лимиты LE со всеми пользователями (50 сертификатов/неделя на зону, 5 дубликатов на имя) | подождать окно, попробовать sslip.io вместо nip.io или свой домен; выданный сертификат сохраняется в томе `caddy_data` |
| Бот молчит, в логах таймауты к `api.telegram.org` | хостер фильтрует исходящий трафик к Telegram | на стенде `TELEGRAM_BOT_MODE=stub` (исходящие — в `tg_outbox`, смотреть `GET /api/v1/internal/tgbot/outbox`); для живой демо — поллинг с машины демонстратора: `ssh -L 5432:localhost:5432 root@<ip>` и локальный процесс бота с `DATABASE_URL` на туннель |
| Ответы LLM-судьи долгие, p50 ≈ 64 с | боевой GigaChat на персональном scope (freemium): один поток, очередь | это ожидаемо: `LLM_TIMEOUT_SECONDS=120`, `LLM_MAX_CONCURRENCY=1`; грейдинг асинхронный (ответ 202, фронт опрашивает статус), эвристический фолбэк по таймауту |
| `compose up` падает с «заполните PUBLIC_DOMAIN» | первый прогон `make prod-deploy` создал `.env` с плейсхолдерами | заполнить `.env` на VPS (§8 шаг 3) и повторить деплой |
| GigaChat: ошибка проверки сертификата | не подложен бандл НУЦ Минцифры или файл с CRLF | выполнить подшаг «Сертификаты НУЦ» §8 (нормализация `openssl x509` обязательна) |
| После смены домена не логинится | redirect URI клиента `fsp-frontend` остались на старом домене | обновить URI в консоли Keycloak или пересоздать стенд (§8, предупреждение) |

## 11. Нагрузочный прогон

`make load-test` (k6; сценарии «подборка» и «раннер теста», цели PLAN §7.5) —
`deploy/load/README.md`.
