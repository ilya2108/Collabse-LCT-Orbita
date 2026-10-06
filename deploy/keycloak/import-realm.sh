#!/bin/bash
# Обёртка импорта realm: Keycloak (Quarkus, 26.x) при `start[-dev] --import-realm`
# НЕ подставляет плейсхолдеры ${env.*} в realm-JSON — подставляем их сами из окружения
# контейнера (секрет confidential-клиента, пароль демо-администратора, публичный URL
# фронта) и запускаем kc.sh.
#
# Использование (entrypoint/command контейнера):
#   /bin/bash /realm-src/import-realm.sh start-dev --import-realm ...
# Входы: REALM_SRC (default /realm-src/realm-fsp.json, монтируется read-only),
#        REALM_DST (default /opt/keycloak/data/import/realm-fsp.json, writable).
set -euo pipefail

SRC="${REALM_SRC:-/realm-src/realm-fsp.json}"
DST="${REALM_DST:-/opt/keycloak/data/import/realm-fsp.json}"

# секреты, без которых realm импортировать нельзя: пустой пароль демо-админа или пустой
# секрет confidential-клиента — ошибка, а не предупреждение
REQUIRED_VARS="DEMO_ADMIN_PASSWORD BACKEND_ADMIN_CLIENT_SECRET"

# значение → строковый литерал JSON (без кавычек) → безопасная замена для sed:
# JSON экранирует \ и ", sed-замена — \, & и разделитель |
escape_value() {
    printf '%s' "$1" \
        | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' \
        | sed -e 's/[\\&|]/\\&/g'
}

mkdir -p "$(dirname "$DST")"
cp "$SRC" "$DST"
for ph in $(grep -o '\${env\.[A-Za-z_][A-Za-z0-9_]*}' "$SRC" | sort -u); do
    var="${ph#\$\{env.}"
    var="${var%\}}"
    val="${!var:-}"
    if [ -z "$val" ]; then
        case " $REQUIRED_VARS " in
            *" $var "*)
                echo "import-realm: ОШИБКА — переменная $var не задана (см. deploy/compose/.env.example)" >&2
                exit 1
                ;;
        esac
        echo "import-realm: ВНИМАНИЕ — переменная $var не задана, плейсхолдер останется пустым" >&2
    fi
    case "$val" in
        *$'\n'*|*$'\r'*)
            echo "import-realm: ОШИБКА — значение $var содержит перевод строки" >&2
            exit 1
            ;;
    esac
    escaped="$(escape_value "$val")"
    sed -i "s|\${env\.$var}|$escaped|g" "$DST"
done

exec /opt/keycloak/bin/kc.sh "$@"
