"""Загрузчик версионируемых промптов `app/modules/llm/prompts/<name>.v<N>.md` (PLAN.md §6).

Файл промпта = YAML-шапка между `---` (имя, версия, температура, ярус модели strong|fast,
имя схемы из `schemas.SCHEMAS`, список переменных, системная инструкция) + тело
пользовательского сообщения с подстановками `{{variable}}`. Строковые переменные
подставляются как есть, остальные — как JSON с отступами (списки, словари).

Версия — часть имени файла; `load_prompt(name)` берёт старшую версию, `load_prompt(name, "v1")`
— конкретную. `template_hash` (sha256 текста файла) пишется в `llm_call.request_json`, чтобы
отличать ответы разных редакций одной версии. Тексты промптов — свои слова, без текста ТЗ.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from app.modules.llm.schemas import SCHEMAS

PROMPTS_DIR = Path(__file__).resolve().parent
MODEL_TIERS = ("strong", "fast")

_FILENAME_RE = re.compile(r"^(?P<name>[a-z][a-z0-9_]*)\.v(?P<version>\d+)\.md$")
_VARIABLE_RE = re.compile(r"\{\{\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(?P<meta>.*?)\n---\s*\n(?P<body>.*)\Z", re.DOTALL)


class LLMPromptError(ValueError):
    """Промпт не найден, шапка некорректна или не хватает переменной при рендере."""


@dataclass(frozen=True)
class RenderedPrompt:
    system: str
    user: str


@dataclass(frozen=True)
class PromptSpec:
    """Промпт одной версии: метаданные из шапки и тело с плейсхолдерами."""

    name: str
    version: str  # "v1"
    temperature: float
    tier: str  # strong | fast
    schema_name: str
    system: str
    body: str
    variables: tuple[str, ...]
    path: Path
    template_hash: str

    @property
    def schema(self) -> type[BaseModel]:
        return SCHEMAS[self.schema_name]

    @property
    def full_name(self) -> str:
        return f"{self.name}.{self.version}"

    def render(self, variables: dict[str, Any]) -> RenderedPrompt:
        """Подстановка переменных; отсутствие объявленной переменной — ошибка, лишние
        переменные игнорируются (но участвуют в хэше вызова — см. journal)."""
        missing = [name for name in self.variables if name not in variables]
        if missing:
            raise LLMPromptError(
                f"промпт {self.full_name}: не переданы переменные {', '.join(missing)}"
            )

        def _substitute(match: re.Match[str]) -> str:
            key = match.group("name")
            if key not in variables:
                raise LLMPromptError(f"промпт {self.full_name}: переменная {key} не передана")
            return format_variable(variables[key])

        return RenderedPrompt(system=self.system, user=_VARIABLE_RE.sub(_substitute, self.body))


def format_variable(value: Any) -> str:
    """Строка — как есть; None — пустая строка; остальное — JSON с отступами, без ASCII-escape."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, BaseModel):
        return value.model_dump_json(indent=2)
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _parse_prompt_file(path: Path) -> PromptSpec:
    match = _FILENAME_RE.match(path.name)
    if match is None:
        raise LLMPromptError(f"имя файла промпта должно быть <name>.v<N>.md: {path.name}")
    text = path.read_text(encoding="utf-8")
    fm = _FRONTMATTER_RE.match(text)
    if fm is None:
        raise LLMPromptError(f"промпт {path.name}: нет YAML-шапки между ---")
    try:
        meta = yaml.safe_load(fm.group("meta")) or {}
    except yaml.YAMLError as exc:
        raise LLMPromptError(f"промпт {path.name}: некорректная YAML-шапка: {exc}") from exc
    if not isinstance(meta, dict):
        raise LLMPromptError(f"промпт {path.name}: шапка должна быть словарём")

    name = match.group("name")
    version = f"v{match.group('version')}"
    if meta.get("name", name) != name:
        raise LLMPromptError(f"промпт {path.name}: name в шапке не совпадает с именем файла")
    schema_name = str(meta.get("schema", ""))
    if schema_name not in SCHEMAS:
        raise LLMPromptError(
            f"промпт {path.name}: schema={schema_name!r} не найдена; доступны "
            f"{', '.join(sorted(SCHEMAS))}"
        )
    tier = str(meta.get("tier", "strong"))
    if tier not in MODEL_TIERS:
        raise LLMPromptError(f"промпт {path.name}: tier должен быть strong или fast")
    try:
        temperature = float(meta.get("temperature", 0.0))
    except (TypeError, ValueError) as exc:
        raise LLMPromptError(f"промпт {path.name}: temperature должна быть числом") from exc
    system = str(meta.get("system", "")).strip()
    if not system:
        raise LLMPromptError(f"промпт {path.name}: пустая системная инструкция (system)")
    body = fm.group("body").strip()
    if not body:
        raise LLMPromptError(f"промпт {path.name}: пустое тело промпта")
    declared = meta.get("variables") or []
    if not isinstance(declared, list):
        raise LLMPromptError(f"промпт {path.name}: variables должен быть списком")
    used = sorted({m.group("name") for m in _VARIABLE_RE.finditer(body)})
    variables = tuple(str(v) for v in declared) if declared else tuple(used)
    unknown = sorted(set(used) - set(variables))
    if unknown:
        raise LLMPromptError(
            f"промпт {path.name}: в теле есть необъявленные переменные {', '.join(unknown)}"
        )
    return PromptSpec(
        name=name,
        version=version,
        temperature=temperature,
        tier=tier,
        schema_name=schema_name,
        system=system,
        body=body,
        variables=variables,
        path=path,
        template_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


@cache
def _catalog(prompts_dir: str) -> dict[str, dict[str, PromptSpec]]:
    """{name: {version: spec}} по всем файлам каталога (кэш на процесс)."""
    result: dict[str, dict[str, PromptSpec]] = {}
    for path in sorted(Path(prompts_dir).glob("*.v*.md")):
        spec = _parse_prompt_file(path)
        result.setdefault(spec.name, {})[spec.version] = spec
    return result


def list_prompts(prompts_dir: Path | None = None) -> list[PromptSpec]:
    """Старшие версии всех промптов каталога, отсортированные по имени."""
    catalog = _catalog(str(prompts_dir or PROMPTS_DIR))
    return [_latest(versions) for _, versions in sorted(catalog.items())]


def _latest(versions: dict[str, PromptSpec]) -> PromptSpec:
    return versions[max(versions, key=lambda v: int(v[1:]))]


def load_prompt(
    name: str, version: str | None = None, prompts_dir: Path | None = None
) -> PromptSpec:
    """Промпт по имени (и версии); неизвестное имя — LLMPromptError с перечнем доступных."""
    catalog = _catalog(str(prompts_dir or PROMPTS_DIR))
    versions = catalog.get(name)
    if not versions:
        raise LLMPromptError(
            f"промпт {name!r} не найден; доступны: {', '.join(sorted(catalog)) or 'нет'}"
        )
    if version is None:
        return _latest(versions)
    spec = versions.get(version)
    if spec is None:
        raise LLMPromptError(
            f"промпт {name}: версия {version} не найдена; есть {', '.join(sorted(versions))}"
        )
    return spec


def reset_prompt_cache() -> None:
    _catalog.cache_clear()


__all__ = [
    "LLMPromptError",
    "MODEL_TIERS",
    "PROMPTS_DIR",
    "PromptSpec",
    "RenderedPrompt",
    "format_variable",
    "list_prompts",
    "load_prompt",
    "reset_prompt_cache",
]
