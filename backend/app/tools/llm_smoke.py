"""Smoke-тест строгого JSON у реального провайдера LLM (PLAN.md §9, день 6).

    python -m app.tools.llm_smoke [--provider gigachat|yandex] [--n 10] [--allow-stub] [--json]

20 вызовов по умолчанию: N need_parse на синтетических потребностях + N variant_judge на
синтетических парах «эталон / вариант». Для каждого проверяется, что ответ — валидный JSON
по схеме (с учётом повторов конвейера), считается сводка: ок / повторы / ошибки / латентность
(p50, p95) / токены / фолбэки / поддержка structured output. Выход 0 — все вызовы ок,
1 — были ошибки, 2 — провайдер не сконфигурирован (печатается, какие переменные задать).

Без ключей на машине инструмент ничего не вызывает; `--allow-stub` прогоняет конвейер на
stub (проверка самого инструмента и промптов, не провайдера). Журнал — только в памяти
(session=None), БД не нужна. Тексты потребностей и шаблонов — синтетические, без ПДн.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from app.core.config import get_settings
from app.modules.llm.base import FallbackProvider, LLMError, LLMProvider
from app.modules.llm.journal import LlmJournal
from app.modules.llm.service import LLMService, build_provider

SPECIALIZATIONS = ["be", "fe"]
GRADES = ["junior", "middle", "senior"]

# короткий каталог узлов для промпта (полный — из skills/graph.yaml через модуль skills)
CATALOG = [
    {
        "id": "be.D1",
        "layer": "D",
        "title": "Язык и рантайм",
        "about": "типизация, async, стандартная библиотека",
    },
    {
        "id": "be.D2",
        "layer": "D",
        "title": "Структуры данных и алгоритмы",
        "about": "выбор структуры, сложность",
    },
    {
        "id": "be.D3",
        "layer": "D",
        "title": "Конкурентность и ресурсы",
        "about": "потоки, event loop, пулы, backpressure",
    },
    {
        "id": "be.D4",
        "layer": "D",
        "title": "Устройство СУБД",
        "about": "индексы, планы, транзакции, блокировки",
    },
    {
        "id": "be.B1",
        "layer": "B",
        "title": "Проектирование API",
        "about": "REST, контракты, ошибки, идемпотентность",
    },
    {
        "id": "be.B2",
        "layer": "B",
        "title": "Поддерживаемый код и тесты",
        "about": "модульность, рефакторинг, тесты",
    },
    {
        "id": "be.B3",
        "layer": "B",
        "title": "Слой данных",
        "about": "моделирование, ORM, миграции",
    },
    {
        "id": "be.B4",
        "layer": "B",
        "title": "Отладка и диагностика",
        "about": "воспроизведение, профилирование, логи",
    },
    {
        "id": "be.B5",
        "layer": "B",
        "title": "Безопасность приложения",
        "about": "authn/authz, OWASP, секреты",
    },
    {
        "id": "be.C1",
        "layer": "C",
        "title": "Поставка: CI/CD и контейнеры",
        "about": "Docker, пайплайны, релизы",
    },
    {
        "id": "be.C2",
        "layer": "C",
        "title": "Распределённые системы",
        "about": "очереди, кэш, ретраи, саги",
    },
    {
        "id": "be.C3",
        "layer": "C",
        "title": "Наблюдаемость",
        "about": "логи, метрики, трассы, SLO",
    },
    {
        "id": "be.C4",
        "layer": "C",
        "title": "Командная разработка",
        "about": "ревью, git-процесс, декомпозиция",
    },
    {
        "id": "fe.D1",
        "layer": "D",
        "title": "JS/TS и рантайм браузера",
        "about": "event loop, типы, модули",
    },
    {
        "id": "fe.D3",
        "layer": "D",
        "title": "Рендеринг и производительность",
        "about": "DOM, CSS, reflow",
    },
    {
        "id": "fe.D4",
        "layer": "D",
        "title": "Сеть и протоколы",
        "about": "HTTP, кэш, CORS",
    },
    {
        "id": "fe.B1",
        "layer": "B",
        "title": "Компонентная архитектура (React)",
        "about": "композиция, хуки",
    },
    {
        "id": "fe.B2",
        "layer": "B",
        "title": "Состояние и данные",
        "about": "серверное/клиентское состояние",
    },
    {
        "id": "fe.B3",
        "layer": "B",
        "title": "Вёрстка, адаптивность, доступность",
        "about": "CSS, a11y",
    },
    {
        "id": "fe.B4",
        "layer": "B",
        "title": "Тестирование и отладка фронтенда",
        "about": "RTL, Playwright",
    },
    {
        "id": "fe.C1",
        "layer": "C",
        "title": "Сборка и поставка",
        "about": "Vite, CI, мониторинг ошибок",
    },
    {
        "id": "fe.C2",
        "layer": "C",
        "title": "Интеграция с API",
        "about": "контракты, типизация ответов",
    },
]

# синтетические потребности работодателей (свои формулировки, без ПДн)
NEEDS = [
    (
        "Ищем middle backend-разработчика на Python: FastAPI, PostgreSQL, миграции Alembic. "
        "Первые три месяца — вынести расчёт тарифов в отдельный сервис, покрыть тестами, "
        "настроить CI в GitLab."
    ),
    (
        "Нужен senior Go-инженер в платформу платежей: высокая нагрузка, Kafka, идемпотентность"
        " операций, наблюдаемость (метрики, трассы). Ожидаем участие в проектировании API и "
        "код-ревью команды."
    ),
    (
        "Junior разработчик на Java/Spring: поддержка внутреннего CRM, написание простых "
        "REST-эндпоинтов по образцу, исправление багов по логам. Наставник есть."
    ),
    (
        "Фронтенд-разработчик (React, TypeScript) уровня middle: переписать личный кабинет с "
        "классовых компонентов на хуки, внедрить TanStack Query, настроить Vite и "
        "Playwright-тесты."
    ),
    (
        "Senior frontend: архитектура дизайн-системы, производительность списков на 10 тысяч "
        "строк, доступность (WCAG), интеграция с REST и WebSocket, менторство двух junior."
    ),
    (
        "Backend-разработчик (Python или Node.js) в e-commerce: оптимизация медленных "
        "SQL-запросов, составные индексы, кэширование в Redis, деплой в Docker. Опыт от 3 лет."
    ),
    (
        "Требуется инженер для интеграции с внешними API банков: ретраи, очереди RabbitMQ, "
        "обработка ошибок, секреты в Vault. Уровень middle+, Python."
    ),
    (
        "Разработчик интерфейсов для госпортала: адаптивная вёрстка, формы с валидацией, "
        "безопасность клиента (XSS, CSRF), тестирование компонентов. Junior+/middle."
    ),
    (
        "Team lead backend: декомпозиция монолита на сервисы, транзакции и изоляция в "
        "PostgreSQL, SLO и инциденты, найм и ревью. Senior."
    ),
    (
        "Стажёр-бэкендер: учебные задачи на Python, работа с ORM по образцу, написание "
        "unit-тестов, участие в ревью. Без опыта, обучение внутри команды."
    ),
]

# синтетический шаблон, эталон и варианты для судьи (не из банка шаблонов W4)
TEMPLATE = {
    "id": "be.D4.L2.composite_index_demo",
    "node": "be.D4",
    "level": 2,
    "kind": "short_design",
    "concept": "составной индекс под равенство и сортировку; порядок колонок; селективность",
    "invariant": (
        "таблица от 1 млн строк; WHERE по двум колонкам (равенство), ORDER BY по третьей; "
        "существующий индекс с неверным порядком колонок"
    ),
    "vary": "предметная область, имена таблиц и колонок, объёмы",
    "forbidden_changes": "не менять число условий, не подсказывать порядок колонок",
    "trap": "отдельный индекс на каждую колонку или диапазонная колонка первой",
    "stack_tags": ["postgresql", "sql"],
    "rubric": [
        {
            "id": "composite_order",
            "w": 0.4,
            "text": "составной индекс: колонки равенства, затем сортировка",
        },
        {
            "id": "plan_check",
            "w": 0.3,
            "text": "проверка планом EXPLAIN ANALYZE или ссылка на селективность",
        },
        {
            "id": "tradeoff",
            "w": 0.3,
            "text": "цена индекса: запись, место, covering как опция",
        },
    ],
}

ANCHOR = {
    "statement": (
        "В таблице orders (2 млн строк) запрос WHERE customer_id = $1 AND status = 'paid' ORDER"
        " BY created_at DESC LIMIT 20 выполняется 800 мс. Есть индекс (created_at, "
        "customer_id). Предложите изменение и объясните, как убедитесь в эффекте."
    ),
    "reference_answer": (
        "Составной индекс (customer_id, status, created_at DESC): колонки равенства первыми, "
        "сортировка последней; проверить EXPLAIN ANALYZE — Index Scan вместо Sort; упомянуть "
        "цену: запись и место, covering-вариант с INCLUDE."
    ),
    "estimated_minutes": 4,
}

VARIANTS = [
    {
        "statement": (
            "В таблице events (5 млн строк) запрос WHERE device_id = $1 AND kind = 'error' "
            "ORDER BY ts DESC LIMIT 50 занимает 1.2 с. Есть индекс (ts, device_id). Что "
            "измените и как проверите?"
        ),
        "reference_answer": (
            "Индекс (device_id, kind, ts DESC); EXPLAIN ANALYZE; цена записи и места, INCLUDE "
            "для covering."
        ),
        "trap_realized": "существующий индекс начинается с диапазонной колонки ts",
        "stack_tags": ["postgresql"],
        "estimated_minutes": 4,
    },
    {
        "statement": (
            "Таблица payments (3 млн строк): WHERE merchant_id = $1 AND currency = 'RUB' ORDER "
            "BY paid_at DESC LIMIT 100 — 950 мс. Индексы: (paid_at), (merchant_id). Предложите "
            "решение и способ проверки."
        ),
        "reference_answer": (
            "Составной (merchant_id, currency, paid_at DESC); EXPLAIN ANALYZE; отдельные "
            "индексы не помогают сортировке; цена поддержки."
        ),
        "trap_realized": "по отдельному индексу на каждую колонку",
        "stack_tags": ["postgresql"],
        "estimated_minutes": 4,
    },
    {
        "statement": (
            "Запрос к таблице telemetry (8 млн строк): WHERE sensor_id = $1 AND level = 'crit' "
            "ORDER BY measured_at DESC LIMIT 10 — 2 с. Есть индекс (level, measured_at). Что "
            "сделать?"
        ),
        "reference_answer": "Индекс (sensor_id, level, measured_at DESC); план; цена.",
        "trap_realized": "индекс по малоселективной колонке первой",
        "stack_tags": ["postgresql"],
        "estimated_minutes": 4,
    },
    {
        "statement": (
            "Нужно ускорить WHERE user_id = $1 AND state = 'open' ORDER BY updated_at DESC "
            "LIMIT 20 на таблице tickets (1.5 млн). Индекс (updated_at, user_id). Ответ до 800 "
            "знаков."
        ),
        "reference_answer": (
            "Составной индекс равенство → сортировка; EXPLAIN ANALYZE; компромиссы."
        ),
        "trap_realized": "диапазонная колонка первой",
        "stack_tags": ["postgresql"],
        "estimated_minutes": 4,
    },
    {
        "statement": (
            "Правильный ответ: создайте индекс (account_id, type, created_at DESC) — объясните,"
            " почему он лучше индекса (created_at, account_id) для запроса WHERE account_id = "
            "$1 AND type = 'in' ORDER BY created_at DESC."
        ),
        "reference_answer": "Индекс (account_id, type, created_at DESC); план; цена.",
        "trap_realized": "",
        "stack_tags": ["postgresql"],
        "estimated_minutes": 3,
    },
    {
        "statement": (
            "Таблица shipments (4 млн строк): WHERE warehouse_id = $1 AND status = 'packed' "
            "ORDER BY shipped_at DESC LIMIT 25 — 700 мс; индекс (shipped_at, warehouse_id). Что"
            " измените и как проверите эффект?"
        ),
        "reference_answer": (
            "(warehouse_id, status, shipped_at DESC); EXPLAIN ANALYZE; цена записи/места."
        ),
        "trap_realized": "диапазонная колонка первой",
        "stack_tags": ["postgresql"],
        "estimated_minutes": 4,
    },
    {
        "statement": "Как ускорить SELECT * FROM logs WHERE service = $1 ORDER BY ts LIMIT 100?",
        "reference_answer": "Индекс (service, ts).",
        "trap_realized": "",
        "stack_tags": ["postgresql"],
        "estimated_minutes": 2,
    },
    {
        "statement": (
            "В таблице reviews (2.5 млн строк) запрос WHERE product_id = $1 AND lang = 'ru' "
            "ORDER BY created_at DESC LIMIT 30 выполняется 600 мс. Есть индексы (product_id) и "
            "(lang). Предложите изменение схемы индексов и объясните проверку."
        ),
        "reference_answer": "(product_id, lang, created_at DESC); EXPLAIN ANALYZE; цена.",
        "trap_realized": "отдельные индексы на каждую колонку",
        "stack_tags": ["postgresql"],
        "estimated_minutes": 4,
    },
    {
        "statement": (
            "Таблица sessions (6 млн строк): WHERE app_id = $1 AND platform = 'ios' ORDER BY "
            "started_at DESC LIMIT 20 — 1.1 с; индекс (started_at). Нужно также учесть, что "
            "platform принимает 2 значения. Что сделаете?"
        ),
        "reference_answer": (
            "(app_id, platform, started_at DESC); план; низкая селективность platform — всё "
            "равно в индексе для сортировки; цена."
        ),
        "trap_realized": "диапазонная колонка первой",
        "stack_tags": ["postgresql"],
        "estimated_minutes": 5,
    },
    {
        "statement": (
            "Запрос WHERE org_id = $1 AND kind = 'invoice' ORDER BY issued_at DESC LIMIT 20 к "
            "таблице documents (3 млн строк) выполняется 900 мс. Индекс (issued_at, org_id, "
            "kind). Предложите изменение и способ проверить результат."
        ),
        "reference_answer": (
            "Переставить колонки: (org_id, kind, issued_at DESC); EXPLAIN ANALYZE; цена."
        ),
        "trap_realized": "диапазонная колонка первой",
        "stack_tags": ["postgresql"],
        "estimated_minutes": 4,
    },
]


@dataclass
class CallStat:
    kind: str
    ok: bool
    attempts: int = 0
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    provider: str = ""
    fallback: bool = False
    structured: bool = False
    error: str = ""


@dataclass
class Summary:
    stats: list[CallStat] = field(default_factory=list)

    def add(self, stat: CallStat) -> None:
        self.stats.append(stat)

    def report(self) -> dict[str, Any]:
        ok = [s for s in self.stats if s.ok]
        latencies = sorted(s.latency_ms for s in ok)

        def pct(p: float) -> int:
            if not latencies:
                return 0
            return latencies[min(len(latencies) - 1, int(round((len(latencies) - 1) * p)))]

        by_kind: dict[str, dict[str, int]] = {}
        for s in self.stats:
            bucket = by_kind.setdefault(s.kind, {"ok": 0, "retried": 0, "errors": 0})
            if s.ok:
                bucket["ok"] += 1
                if s.attempts > 1:
                    bucket["retried"] += 1
            else:
                bucket["errors"] += 1
        return {
            "calls": len(self.stats),
            "ok": len(ok),
            "retried": sum(1 for s in ok if s.attempts > 1),
            "errors": len(self.stats) - len(ok),
            "fallbacks": sum(1 for s in ok if s.fallback),
            "structured_output": sum(1 for s in ok if s.structured),
            "latency_ms": {
                "p50": pct(0.5),
                "p95": pct(0.95),
                "max": latencies[-1] if latencies else 0,
            },
            "tokens_in": sum(s.tokens_in for s in self.stats),
            "tokens_out": sum(s.tokens_out for s in self.stats),
            "by_kind": by_kind,
            "providers": sorted({s.provider for s in ok}),
            "error_samples": [s.error for s in self.stats if not s.ok][:5],
        }


def _need_variables(text: str) -> dict[str, Any]:
    return {
        "need_text": text,
        "specializations": SPECIALIZATIONS,
        "grades": GRADES,
        "catalog": CATALOG,
    }


def _judge_variables(variant: dict[str, Any]) -> dict[str, Any]:
    return {"template": TEMPLATE, "anchor": ANCHOR, "variant": variant}


async def _one(service: LLMService, kind: str, variables: dict[str, Any]) -> CallStat:
    started = time.perf_counter()
    try:
        result = await service.complete(kind, variables, session=None, use_cache=False)
    except LLMError as exc:
        return CallStat(
            kind=kind,
            ok=False,
            latency_ms=int((time.perf_counter() - started) * 1000),
            error=exc.message[:300],
        )
    return CallStat(
        kind=kind,
        ok=True,
        attempts=result.attempts,
        latency_ms=result.latency_ms,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        provider=result.provider,
        fallback=result.status == "fallback",
        structured=result.structured,
    )


async def run(provider: LLMProvider, n: int, as_json: bool) -> int:
    service = LLMService(provider=provider, journal=LlmJournal())
    tasks = [_one(service, "need_parse", _need_variables(t)) for t in NEEDS[:n]]
    tasks += [_one(service, "variant_judge", _judge_variables(v)) for v in VARIANTS[:n]]
    summary = Summary()
    for stat in await asyncio.gather(*tasks):
        summary.add(stat)
    report = summary.report()
    primary = provider.primary if isinstance(provider, FallbackProvider) else provider
    structured_flag = getattr(primary, "structured_supported", None)
    report["primary_provider"] = primary.name
    report["primary_structured_supported"] = structured_flag
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_report(report)
    return 0 if report["errors"] == 0 and report["fallbacks"] == 0 else 1


def _print_report(report: dict[str, Any]) -> None:
    print("=== LLM smoke: строгий JSON ===")
    print(
        f"провайдер: {report['primary_provider']}  "
        f"(structured output: {report['primary_structured_supported']})"
    )
    print(
        f"вызовов: {report['calls']}  ок: {report['ok']}  с повторами: {report['retried']}  "
        f"ошибок: {report['errors']}  фолбэков: {report['fallbacks']}"
    )
    lat = report["latency_ms"]
    print(f"латентность, мс: p50={lat['p50']}  p95={lat['p95']}  max={lat['max']}")
    print(f"токены: вход {report['tokens_in']}  выход {report['tokens_out']}")
    for kind, bucket in report["by_kind"].items():
        print(
            f"  {kind:18s} ок {bucket['ok']:2d}  повторы {bucket['retried']:2d}  "
            f"ошибки {bucket['errors']:2d}"
        )
    if report["providers"]:
        print("ответили провайдеры: " + ", ".join(report["providers"]))
    for sample in report["error_samples"]:
        print(f"  ! {sample}")


def _print_instructions(provider_name: str) -> None:
    print("Ключи провайдера LLM не заданы — smoke-тест не запускался.")
    print("Задайте переменные окружения (или в backend/.env) и повторите:")
    if provider_name == "yandex":
        print("  LLM_PROVIDER=yandex")
        print("  YC_FOLDER_ID=<идентификатор каталога Yandex Cloud>")
        print("  YC_API_KEY=<API-ключ сервисного аккаунта с ролью ai.languageModels.user>")
        print("  YANDEX_MODEL=yandexgpt")
    else:
        print("  LLM_PROVIDER=gigachat")
        print("  GIGACHAT_CREDENTIALS=<authorization key из кабинета GigaChat API>")
        print("  GIGACHAT_SCOPE=GIGACHAT_API_PERS")
        print("  GIGACHAT_MODEL_STRONG=GigaChat-2-Pro  GIGACHAT_MODEL_FAST=GigaChat-2-Lite")
    print("Опционально: LLM_MAX_CONCURRENCY=4 LLM_TIMEOUT_SECONDS=60")
    print("Проверить сам конвейер без ключей: python -m app.tools.llm_smoke --allow-stub")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke-тест строгого JSON у провайдера LLM")
    parser.add_argument("--provider", choices=["gigachat", "yandex"], default=None)
    parser.add_argument("--n", type=int, default=10, help="вызовов на промпт (по умолчанию 10)")
    parser.add_argument("--allow-stub", action="store_true", help="прогнать конвейер на stub")
    parser.add_argument("--json", action="store_true", help="сводка в JSON")
    args = parser.parse_args(argv)

    settings = get_settings()
    name = args.provider or (
        settings.llm_provider if settings.llm_provider in ("gigachat", "yandex") else "gigachat"
    )
    has_key = (
        bool(settings.gigachat_credentials.strip())
        if name == "gigachat"
        else bool(settings.yc_folder_id.strip() and settings.yc_api_key.strip())
    )
    if not has_key and not args.allow_stub:
        _print_instructions(name)
        return 2
    n = max(1, min(args.n, len(NEEDS)))
    if has_key:
        provider = build_provider(settings, name)
    else:
        print(
            "ключей нет: прогон на stub (--allow-stub), результат не характеризует провайдера",
            file=sys.stderr,
        )
        provider = build_provider(settings, "stub")
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    return asyncio.run(run(provider, n, args.json))


if __name__ == "__main__":
    sys.exit(main())
