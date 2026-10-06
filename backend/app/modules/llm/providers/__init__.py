"""Провайдеры LLM: gigachat (основной), yandex (резерв), replay (кэш из репозитория),
rules (словарь ключевых слов для need_parse), stub (фикстуры и эвристики для CI).

SDK провайдеров импортируются лениво внутри модулей — импорт пакета не требует ключей
и сетевых зависимостей."""

from app.modules.llm.providers.replay import ReplayProvider
from app.modules.llm.providers.rules import RulesProvider
from app.modules.llm.providers.stub import StubProvider

__all__ = ["ReplayProvider", "RulesProvider", "StubProvider"]
