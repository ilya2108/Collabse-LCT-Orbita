# frontend «Орбита ФСП» — React + Vite → nginx-unprivileged (uid 101, порт 8080 внутри).
# Один образ для любого окружения: рантайм-конфиг /config.js (window.__ENV__) генерируется
# при старте контейнера из переменных KEYCLOAK_URL, KEYCLOAK_REALM, KEYCLOAK_CLIENT_ID,
# API_BASE; nginx.conf проксирует /api на backend:8000 и отдаёт SPA.
# Ожидания от исходников: package-lock.json и `npm run build` → dist/; index.html подключает
# <script src="/config.js"> до бандла (в dev — public/config.js с пустым объектом).

# --- build ---
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# --- runtime ---
FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY --from=build --chown=101:101 /app/dist /usr/share/nginx/html
# nginx.conf — шаблон: entrypoint образа подставляет ${KEYCLOAK_URL} (адрес для CSP) и кладёт
# результат в /etc/nginx/conf.d/default.conf; фильтр — чтобы $host/$uri остались для nginx
COPY nginx.conf /etc/nginx/templates/default.conf.template
ENV NGINX_ENVSUBST_FILTER='^KEYCLOAK_URL$'
# генератор /config.js выполняется штатным entrypoint'ом nginx (каталог /docker-entrypoint.d)
COPY --chmod=755 <<'EOF' /docker-entrypoint.d/40-config-js.sh
#!/bin/sh
# Рантайм-конфиг фронта (12-factor): window.__ENV__ из переменных окружения контейнера.
set -eu
TARGET=/usr/share/nginx/html/config.js
cat > "$TARGET" <<JS
window.__ENV__ = {
  KEYCLOAK_URL: "${KEYCLOAK_URL}",
  KEYCLOAK_REALM: "${KEYCLOAK_REALM}",
  KEYCLOAK_CLIENT_ID: "${KEYCLOAK_CLIENT_ID}",
  API_BASE: "${API_BASE}"
};
JS
echo "40-config-js: сгенерирован $TARGET (KEYCLOAK_URL=${KEYCLOAK_URL})"
EOF
# Дефолты соответствуют docker compose --profile full (фронт на http://localhost:3000)
ENV KEYCLOAK_URL=http://localhost:8080 \
    KEYCLOAK_REALM=fsp \
    KEYCLOAK_CLIENT_ID=fsp-frontend \
    API_BASE=/api
EXPOSE 8080
