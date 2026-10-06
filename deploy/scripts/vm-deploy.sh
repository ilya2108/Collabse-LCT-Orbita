#!/bin/sh
# Деплой стенда на VPS через docker compose. Прод-профиль (Caddy с авто-TLS, Keycloak за
# /auth, Mailpit за basic-auth): make prod-deploy → COMPOSE_FILE=docker-compose.prod.yml,
# PUBLIC_BASE=https://<домен>. Порядок и переменные .env — docs/08-run.md §8.
#
# Использование:
#   VM_HOST=root@1.2.3.4 ./deploy/scripts/vm-deploy.sh            # deploy (по умолчанию)
#   VM_HOST=root@1.2.3.4 ./deploy/scripts/vm-deploy.sh smoke      # только проверка URL
# Переменные: VM_HOST (обязательна), VM_DIR (default /opt/fsp-orbita),
#             COMPOSE_FILE (default deploy/compose/docker-compose.dev.yml),
#             PUBLIC_BASE (default http://<host без user@>:3000) — для smoke.
#
# deploy: rsync репозитория без .git/.venv/node_modules/dist/docs/source/PLAN.md/.env →
#         на VPS gen-dotenv.sh (ключи ПДн стабильны: существующий .env не перетирается) →
#         docker compose --profile full up -d --build → smoke.
# Требования на VPS: docker + compose v2, rsync, ssh-ключ (см. docs/08-run.md).
#
# ЗАЩИТА: dev-compose (Keycloak start-dev, порты наружу, известные пароли, Mailpit без
# пароля) на публичном адресе запрещён — скрипт отказывает, пока COMPOSE_FILE указывает
# на *dev*. Осознанный запуск dev-стека на изолированной машине: ALLOW_DEV_STACK=1.
set -eu

VM_HOST="${VM_HOST:?задайте VM_HOST=user@host}"
VM_DIR="${VM_DIR:-/opt/fsp-orbita}"
COMPOSE_FILE="${COMPOSE_FILE:-deploy/compose/docker-compose.dev.yml}"
HOST_ONLY="${VM_HOST#*@}"
PUBLIC_BASE="${PUBLIC_BASE:-http://$HOST_ONLY:3000}"
ACTION="${1:-deploy}"

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

smoke() {
    echo "smoke: $PUBLIC_BASE"
    for path in /healthz /api/v1/health/ready; do
        code="$(curl -s -o /dev/null -w '%{http_code}' "$PUBLIC_BASE$path" || true)"
        echo "  $path → $code"
        [ "$code" = "200" ] || { echo "smoke: $path не отвечает 200" >&2; return 1; }
    done
    # прод: Keycloak не публикует порт — проверяем через Caddy по под-пути /auth
    case "$COMPOSE_FILE" in
        *prod*) kc="$PUBLIC_BASE/auth/realms/fsp/.well-known/openid-configuration" ;;
        *) kc="http://$HOST_ONLY:8080/realms/fsp/.well-known/openid-configuration" ;;
    esac
    code="$(curl -s -o /dev/null -w '%{http_code}' "$kc" || true)"
    echo "  keycloak realm fsp → $code"
    [ "$code" = "200" ] || { echo "smoke: realm fsp недоступен" >&2; return 1; }
    echo "smoke: ok"
}

guard_compose_file() {
    case "$COMPOSE_FILE" in
        *dev*)
            if [ "${ALLOW_DEV_STACK:-0}" != "1" ]; then
                echo "vm-deploy: $COMPOSE_FILE — dev-стек на VPS запрещён (Keycloak start-dev," >&2
                echo "  известные пароли, все порты наружу). Используйте прод-профиль" >&2
                echo "  (COMPOSE_FILE=deploy/compose/docker-compose.prod.yml) или, только для" >&2
                echo "  изолированной машины, ALLOW_DEV_STACK=1." >&2
                exit 2
            fi
            echo "vm-deploy: ВНИМАНИЕ — dev-стек разрешён явно (ALLOW_DEV_STACK=1)" >&2
            ;;
    esac
}

deploy() {
    guard_compose_file
    echo "deploy: rsync → $VM_HOST:$VM_DIR"
    ssh "$VM_HOST" "mkdir -p '$VM_DIR'"
    rsync -az --delete \
        --exclude .git --exclude '.venv' --exclude node_modules --exclude dist \
        --exclude docs/source --exclude PLAN.md --exclude '.env' --exclude '*.env' \
        --exclude '.claude' --exclude '.DS_Store' \
        "$ROOT/" "$VM_HOST:$VM_DIR/"
    echo "deploy: окружение и compose на VPS"
    ssh "$VM_HOST" "cd '$VM_DIR' && ./deploy/scripts/gen-dotenv.sh \
        && docker compose -f '$COMPOSE_FILE' --profile full up -d --build --remove-orphans"
    smoke
}

case "$ACTION" in
    deploy) deploy ;;
    smoke) smoke ;;
    *) echo "vm-deploy: неизвестное действие '$ACTION' (deploy|smoke)" >&2; exit 2 ;;
esac
