# Описание API

Статус: draft (11 октября, день сдачи). Справка написана по сгенерированному
[`openapi.json`](openapi.json) (50 путей, заголовок «„Орбита ФСП" — API 0.1.0»).
Живой Swagger UI — на стенде: <https://orbita.185.239.51.92.nip.io/api/v1/docs>
(локально — `http://localhost:8000/api/v1/docs`). При расхождении текста и спецификации
прав `openapi.json`/Swagger — он генерируется из кода.

## 1. Общие соглашения

- **Префикс** — `/api/v1`. Исключение — пробы здоровья: `GET /healthz`, `GET /health/live`,
  `GET /health/ready` живут в корне приложения и в Swagger не показываются.
- **Аутентификация** — Bearer JWT Keycloak (realm `fsp`, клиент `fsp-frontend`, PKCE).
  Keycloak только аутентифицирует; роли живут в БД (`app_user.roles[]`), `admin` — только
  из токена (отзыв в Keycloak действует немедленно). Авторизация — `user.effective_roles`.
- **Служебный контур** — `/api/v1/internal/*` и `/api/v1/assessment/internal/*` по заголовку
  `X-Internal-Token`; на стенде эти пути не проксируются nginx наружу.
- **Формат ошибки** — плоское тело `{code, message, details, request_id}`; каждый ответ несёт
  заголовок `X-Request-Id`. Коды: 400 `validation_error` (с `details[{field, code, message}]`,
  сюда же сведены ошибки Pydantic), 401 `unauthorized`, 403 `forbidden` (в т. ч. отсутствие
  согласия на ПДн), 404 `not_found`, 409 `conflict` (уточняющие коды вида `role_already_set`,
  `invitation_quota`), 422 `unprocessable`, 429 `rate_limited`, 501 `not_implemented`,
  503 `integration_unavailable`.
- **Пагинация** — query-параметры `limit` (1–200, по умолчанию 25), `offset` (≥0), `sort`;
  ответ-конверт `{items, total, limit, offset}` (`core/pagination.py`).
- **ПДн** в ответах — только при наличии согласия и основания: до раскрытия контактов
  кандидаты везде представлены псевдонимами («B-7F3A»), контакты отдаёт единственный
  эндпоинт `GET /invitations/{id}/contacts` с записью в аудит.

## 2. Группы эндпоинтов

### auth — вход, роль, согласия

| Метод и путь | Назначение | Роль |
|---|---|---|
| `GET /auth/me` | Текущий пользователь: роли, согласия, флаги | вошедший |
| `POST /auth/role` | Выбор роли при первом входе (candidate \| employer); 409 `role_already_set` | вошедший |
| `POST /auth/consent` | Согласие на обработку ПДн (версия политики) и отдельно — на публикацию | вошедший |
| `DELETE /auth/account` | Отзыв согласия и удаление персональных данных | вошедший |
| `POST /auth/telegram/link-token` | Одноразовый код привязки Telegram-чата (показывается один раз) | вошедший |
| `GET /auth/telegram/me` | Статус привязки Telegram | вошедший |

### skills — справочник графа навыков (без аутентификации)

| Метод и путь | Назначение | Роль |
|---|---|---|
| `GET /skills/industries` | Справочник отраслей | публичный |
| `GET /skills/specializations` | Специализации: `active` (с тестом) и `preview` (только граф) | публичный |
| `GET /skills/graph/{spec}` | Граф специализации: слои, узлы, рёбра, мосты | публичный |
| `GET /skills/grade-profiles/{spec}` | Профили грейдов (полные и демо) | публичный |

### candidates — профиль и категория кандидата

| Метод и путь | Назначение | Роль |
|---|---|---|
| `GET /candidates/me` | Мой профиль | candidate |
| `PUT /candidates/me` | Обновить профиль (псевдоним, город, отрасль, стек; ПДн — при согласии) | candidate |
| `GET /candidates/me/category` | Моя категория (специализация × подтверждённый грейд) | candidate |
| `GET /candidates/me/invitations` | Мои входящие приглашения | candidate |

### assessment — тестирование и целостность сессии

Реализовано в спецификации на момент генерации:

| Метод и путь | Назначение | Роль |
|---|---|---|
| `GET /assessment/templates` | Каталог шаблонов заданий (метаданные, без ответов) | admin |
| `POST /assessment/sessions/{id}/events` | Батч телеметрии раннера (только длины и тайминги, текстов нет) | candidate-владелец |
| `GET /assessment/sessions/{id}/integrity` | Агрегат целостности `high/medium/low` | владелец или admin |
| `GET /assessment/internal/templates` | Тот же каталог для джоб | `X-Internal-Token` |

Путь кандидата (раннер теста) — контракт достраивающегося модуля `assessment/sessions`
(сервисный слой и схемы в коде, миграция `0005_candidate_path`; итоговые пути — в живом
Swagger стенда):

| Метод и путь | Назначение | Роль |
|---|---|---|
| `POST /assessment/sessions` | Создать сессию `{target_grade}` (201) или вернуть активную (200); режим на старте: экспресс/полный (см. [`02-testing.md`](02-testing.md) §4) | candidate |
| `GET /assessment/sessions/current` | Активная сессия или 404 | candidate |
| `GET /assessment/sessions/{id}/items/current` | Текущее задание — по одному, без эталонов и рубрик | candidate |
| `POST /assessment/sessions/{id}/items/{item_id}/answer` | Сохранить ответ (choice — детерминированная проверка по ключу; text — LLM-судья `answer_grade`) | candidate |
| `POST /assessment/sessions/{id}/finish` | Грейдинг, подтверждения узлов, снимок, категория, итог | candidate |

### llm — наблюдаемость провайдера

| Метод и путь | Назначение | Роль |
|---|---|---|
| `GET /llm/usage` | Сводка использования LLM за день | internal или admin |
| `GET /llm/prompts` | Список промптов и их переменных | admin |
| `POST /llm/dry-run` | Прогон промпта на stub-провайдере (отладка) | admin |

### employers — компания, участники, потребности

| Метод и путь | Назначение | Роль |
|---|---|---|
| `GET /employers/me/company` | Моя компания (личный профиль создаётся при первом обращении) | employer |
| `PUT /employers/me/company` | Обновить компанию (контакты — ПДн, шифруются) | employer |
| `GET /employers/me/company/members` | Участники компании (ПДн маскированы) | employer-owner |
| `POST /employers/me/company/members/invite` | Пригласить эйчара/менеджера (одноразовый токен — только в этом ответе) | employer-owner |
| `DELETE /employers/me/company/members/{member_id}` | Отозвать участника или приглашение | employer-owner |
| `GET /employers/company/invites/{token}` | Информация о приглашении по токену (страница принятия, бот) | по токену |
| `POST /employers/company/invites/accept` | Принять приглашение в компанию (роль employer дописывается) | вошедший |
| `GET /needs` · `POST /needs` | Потребности компании; создание — зарплата «от–до» обязательна | employer |
| `GET /needs/{id}` · `PUT /needs/{id}` | Чтение и обновление потребности | employer |

### matching — подборка, воронка, приглашения, контакты

| Метод и путь | Назначение | Роль |
|---|---|---|
| `GET /needs/{id}/candidates` | Ранжированные кандидаты с объяснением позиции («почему здесь») | employer |
| `GET /needs/{id}/pipeline` | Воронка стадий (кандидаты — псевдонимами) | employer |
| `POST /needs/{id}/pipeline` | Добавить кандидата в шорт-лист | employer |
| `PATCH /needs/{id}/pipeline/{candidate_id}` | Перевести стадию кандидата | employer |
| `POST /invitations` | Пригласить кандидата; зарплата «от–до» обязательна, лимиты квот | employer |
| `GET /employers/me/invitations` | Приглашения моей компании | employer |
| `GET /invitations/{id}` | Приглашение по id (каждой стороне — своё представление) | обе стороны |
| `POST /invitations/{id}/view` | `sent → viewed` | candidate |
| `POST /invitations/{id}/accept` | Принять: контакты открываются обеим сторонам | candidate |
| `POST /invitations/{id}/decline` | Отклонить (с причиной) | candidate |
| `GET /invitations/{id}/contacts` | Контакты кандидата — только после принятия; пишется аудит | employer |

### tma — Telegram Mini App

Зеркало веб-контрактов для мини-аппа (аутентификация — initData Telegram):
`GET /tma/me`, `GET /tma/invitations`, `POST /tma/invitations/{id}/accept|decline`,
`GET /tma/needs`, `GET /tma/needs/{id}/pipeline`, `PATCH /tma/needs/{id}/pipeline/{candidate_id}` —
те же правила стадий, псевдонимов и раскрытия контактов, что и в вебе.

### internal — служебный контур (`X-Internal-Token`)

| Метод и путь | Назначение |
|---|---|
| `POST /internal/ping` | Проверка внутреннего токена |
| `GET /internal/llm/usage` | Сводка вызовов LLM (провайдер, дневные счётчики, журнал) |
| `POST /internal/demo/reset` | Пересев демо-данных для жюри |
| `POST /internal/digest/run` | Разослать дайджест всем привязанным чатам сейчас (демо) |
| `GET /internal/tgbot/outbox` | Журнал исходящих бота (stub-отправка и дедупликация) |

Группа `fsp` (привязка ФСП ID, импорт достижений) на момент генерации спецификации — пустой
каркас роутера; концепция интеграции — [`05-fsp-integration.md`](05-fsp-integration.md).

## 3. Примеры

Токен демо-аккаунта (Keycloak, grant `password`, публичный клиент `fsp-frontend`):

```sh
TOKEN=$(curl -s "https://<домен>/auth/realms/fsp/protocol/openid-connect/token" \
  -d grant_type=password -d client_id=fsp-frontend \
  -d username=<демо-логин> -d password=<пароль из материалов сдачи> | jq -r .access_token)

curl -s -H "Authorization: Bearer $TOKEN" "https://<домен>/api/v1/auth/me"
curl -s -H "Authorization: Bearer $TOKEN" "https://<домен>/api/v1/candidates/me/category"
```

Сценарии кликами — [`demo-script.md`](demo-script.md); запуск стенда и демо-аккаунты —
[`08-run.md`](08-run.md).

## 4. Как обновить документ

```sh
cd backend && .venv/bin/python -c \
  "from app.main import app; import json; print(json.dumps(app.openapi(), ensure_ascii=False))" \
  > ../docs/openapi.json
```

Таблицы раздела 2 сверяются с `docs/openapi.json` вручную (по `tags` операций); источник
правды — код роутеров.
