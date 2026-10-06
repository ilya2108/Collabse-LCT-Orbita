# Frontend «Орбиты ФСП»

React 18 + TypeScript 5 + Vite 6 + Tailwind v4 + shadcn/ui (UI-кит «Альмы», перекрашенный под
брендбук ФСП), TanStack Query 5, react-router 6, keycloak-js 26, ECharts (граф навыков).

## Запуск

```bash
npm ci
npm run dev        # http://localhost:5173, прокси /api → http://localhost:8000 (backend)
npm run dev:mock   # режим фикстур: UI без backend и Keycloak (src/shared/api/mock.ts)
npx tsc --noEmit   # типы
npm run build      # tsc + vite build → dist/
```

Конфигурация в рантайме — `window.__ENV__` из `/config.js` (в контейнере генерируется из
`config.js.template` скриптом `docker/40-config-js.sh`; в dev отдаётся пустой `public/config.js`),
ниже по приоритету — переменные сборки `VITE_*`, затем дефолты `src/shared/config/env.ts`
(Keycloak `http://localhost:8080`, realm `fsp`, клиент `fsp-frontend`).

## Структура

```
src/app/            провайдеры, роутер и guard'ы онбординга, лэйаут кабинетов, navigation.ts (меню по роли)
src/pages/          login, choose-role, consent, кабинет кандидата, кабинет работодателя, граф навыков, 403/404
src/content/        текст политики ПДн (POLICY_VERSION уходит в POST /auth/consent)
src/shared/api/     fetch-клиент (Bearer, X-Request-Id, плоские ошибки {code, message, details, request_id}),
                    типы контракта, endpoints/*, mock.ts (фикстуры)
src/shared/auth/    keycloak.ts (PKCE, login/register/logout), AuthContext (роли и согласия из /auth/me), roles.ts
src/shared/ui/      кит компонентов; states.tsx — EmptyState/ErrorState/LoadingState
src/shared/config/  tokens.ts (hex палитры для ECharts/SVG), echartsTheme.ts, env.ts
src/shared/illustrations/  собственные нейтральные SVG-сюжеты (без логотипов ФСП)
src/styles/globals.css     токены палитры (OKLCH) и Montserrat
```

## Онбординг и guard'ы

`/login` → Keycloak (вход / регистрация с подтверждением email) → `GET /auth/me`:
без роли → `/choose-role` (`POST /auth/role`), без согласия → `/consent`
(`POST /auth/consent {policy_version, publish}`), затем кабинет активной роли:
`/candidate/*` или `/employer/*`; `/skills/graph/:spec` доступен обеим ролям внутри их лэйаута.

## Палитра и контраст

primary `#FF0053` даёт на белом 3.9:1, поэтому розовый — только крупные элементы
(CTA `variant="brand" size="lg"`, индикаторы, граф, иллюстрации); обычные кнопки — тёмный
`#310F53` с белым текстом, розовый текстом — `--primary-deep` `#C40040`. Подробности и
все значения — в шапке `src/styles/globals.css`.
