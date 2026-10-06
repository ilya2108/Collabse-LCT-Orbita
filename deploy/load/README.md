# Нагрузочное тестирование (k6) — заготовка

Сценарий `k6-script.js` проверяет цели производительности PLAN.md §7.5: подборка p95 ≤ 500 мс,
карточка ≤ 200 мс, поиск ≤ 300 мс, приём ответа в раннере теста ≤ 300 мс. Прогон — 10 октября
(таймбокс 1 час), результат — `docs/validation-report.md` §H. Артефакт `summary.json` в `.gitignore`.

## Предусловия

1. Стек поднят и просижен: `make dev && make seed` (LLM в режиме `stub` — токены не расходуются).
2. Токены пользователей: `EMPLOYER_TOKEN` (работодатель с компанией), `CANDIDATE_TOKEN`
   (кандидат с согласием и профилем). Берутся из браузера после входа; при необходимости
   добавим confidential-клиент с Direct Access Grants только для нагрузочного прогона.

## Запуск

```sh
# локальный бинарь k6
EMPLOYER_TOKEN=… CANDIDATE_TOKEN=… make load-test
# короткий санити-прогон
SMOKE=1 EMPLOYER_TOKEN=… k6 run deploy/load/k6-script.js

# k6 в Docker (бинарь не нужен); backend на хосте виден как host.docker.internal
docker run --rm -i --add-host host.docker.internal:host-gateway \
  -e BASE_URL=http://host.docker.internal:8000 -e EMPLOYER_TOKEN -e CANDIDATE_TOKEN \
  grafana/k6:0.57.0 run - < deploy/load/k6-script.js
```

Пороги зашиты в скрипт (`thresholds`): k6 завершится ненулевым кодом при нарушении SLA.
