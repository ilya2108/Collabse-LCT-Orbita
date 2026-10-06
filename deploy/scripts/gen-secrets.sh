#!/bin/sh
# Генерация gitignored-файлов окружения для произвольного каталога (прод-стенд, VPS).
#
# Использование: ./deploy/scripts/gen-secrets.sh <dir>
#   например:    ./deploy/scripts/gen-secrets.sh deploy/compose
#
# Для каждого <dir>/*.env.example создаётся <dir>/*.env со случайными значениями вместо
# плейсхолдеров __GEN_*__. Существующие *.env не трогаются: повторный запуск ничего
# не перетирает — ключ шифрования ПДн стабилен между деплоями.
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/genlib.sh"

TARGET_DIR="${1:?использование: gen-secrets.sh <dir>, например deploy/compose}"

if [ ! -d "$TARGET_DIR" ]; then
    echo "gen-secrets: каталог $TARGET_DIR не найден" >&2
    exit 1
fi

echo "gen-secrets: $TARGET_DIR"
found=0
for example in "$TARGET_DIR"/*.env.example "$TARGET_DIR"/.env.example; do
    [ -f "$example" ] || continue
    found=1
    fill_env_file "$example" "${example%.example}"
done

if [ "$found" -eq 0 ]; then
    echo "gen-secrets: в $TARGET_DIR нет *.env.example" >&2
    exit 1
fi
