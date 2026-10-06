#!/bin/sh
# Генерация deploy/compose/.env из deploy/compose/.env.example (цель `make dev-env`).
# Заполняются только __GEN_*__ плейсхолдеры (ключи ПДн, внутренний токен, секрет клиента
# Keycloak); существующий .env не перезаписывается — ключ шифрования ПДн стабилен между
# запусками, иначе зашифрованные данные в БД стали бы нечитаемыми.
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/genlib.sh"

COMPOSE_DIR="$SCRIPT_DIR/../compose"

echo "gen-dotenv: deploy/compose/.env"
fill_env_file "$COMPOSE_DIR/.env.example" "$COMPOSE_DIR/.env"
