"""Провайдер `rules`: детерминированный разбор потребности по словарю ключевых слов.

Словарь — `app/skills/keywords.yaml` (зона W2). Поддерживаются два формата:

    # плоский (актуальный файл W2)
    specializations: {be: [бэкенд, backend, …], fe: […], data: […]}   # слова специализации
    nodes: {be.D4: [индекс, транзакц, postgres], fe.B1: [react, …]}   # узел → слова
    unsupported_specializations:
      - {code: mobile, title: Мобильная разработка, nearest: fe, keywords: [android, ios]}

    # вложенный
    be: {be.B1: [rest, api], be.D4: [postgres, индекс]}
    unsupported_specializations: {mobile: [android, ios]}

Сопоставление (как в `modules/skills/service.py::match_keywords`): без регистра и ё/е;
слово длиннее 3 символов без знаков совпадает с началом слова в тексте («типизац» →
«типизацию»), короткие слова и слова со знаками («c#», "ci/cd") — целиком по границам.
Файла нет → пустой словарь, профиль с предупреждением.

Направление вне MVP (unsupported) побеждает по совпадениям → `specialization: null`,
предупреждение `specialization_not_supported:<code>` и подсказка ближайшей (`nearest`);
с явной подсказкой `specialization=` разбор идёт по ней. Умеет только `need_parse`;
остальные промпты → `LLMUnsupportedError`, и цепочка `service.build_provider` отдаёт их stub.
Чистая функция `parse_need_by_rules` доступна модулям matching / candidates.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from app.modules.llm.base import CompletionResult, LLMProvider, LLMUnsupportedError, RawCompletion
from app.modules.llm.prompts import PromptSpec
from app.modules.llm.schemas import NeedNode, NeedProfile

logger = logging.getLogger("app.llm.rules")

KEYWORDS_FILENAME = "keywords.yaml"
UNSUPPORTED_KEY = "unsupported_specializations"
_RESERVED_KEYS = {UNSUPPORTED_KEY, "specializations", "nodes", "stack_tags", "grades", "version"}
WARNING_NOT_SUPPORTED = "specialization_not_supported"

# встроенный список технологий для stack_tags — используется, если словарь его не задаёт
DEFAULT_STACK_TAGS: tuple[str, ...] = (
    "python", "fastapi", "django", "flask", "aiohttp", "sqlalchemy", "celery",
    "java", "spring", "kotlin", "go", "golang", "rust", "c#", ".net", "php", "laravel",
    "node.js", "nodejs", "nestjs", "express", "typescript", "javascript", "react", "vue",
    "angular", "next.js", "svelte", "redux", "vite", "webpack", "tailwind", "css", "html",
    "postgresql", "postgres", "mysql", "clickhouse", "mongodb", "redis", "kafka", "rabbitmq",
    "elasticsearch", "sql", "graphql", "grpc", "rest", "docker", "kubernetes", "k8s",
    "terraform", "ansible", "nginx", "linux", "git", "ci/cd", "gitlab", "github actions",
    "prometheus", "grafana", "sentry", "jest", "vitest", "playwright", "cypress", "pytest",
    "keycloak", "oauth", "jwt", "s3", "aws", "yandex cloud",
)  # fmt: skip

DEFAULT_GRADE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "senior": ("senior", "сеньор", "синьор", "ведущий", "старший", "lead", "лид", "архитектор",
               "principal", "staff", "head of"),
    "middle": ("middle", "мидл", "middle+", "мидл+", "уверенный", "самостоятельн"),
    "junior": ("junior", "джун", "джуниор", "младший", "начинающ", "стажёр", "стажер",
               "intern", "без опыта", "trainee"),
}  # fmt: skip

_LEVEL_BY_GRADE = {"junior": 1, "middle": 2, "senior": 3}
_WORD_RE = re.compile(r"[a-zа-яё0-9+#.]+")
_PLAIN_WORD_RE = re.compile(r"^[a-zа-яё0-9]+$")


@dataclass(frozen=True)
class KeywordMatch:
    node_id: str
    keywords: tuple[str, ...]

    @property
    def hits(self) -> int:
        return len(self.keywords)


@dataclass(frozen=True)
class UnsupportedSpec:
    code: str
    keywords: tuple[str, ...]
    title: str = ""
    nearest: str | None = None


@dataclass
class Keywords:
    """Загруженный словарь: узлы по специализациям, слова специализаций, направления вне MVP,
    теги стека и ключевые слова грейдов."""

    specializations: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    spec_keywords: dict[str, list[str]] = field(default_factory=dict)
    unsupported: list[UnsupportedSpec] = field(default_factory=list)
    stack_tags: list[str] = field(default_factory=lambda: list(DEFAULT_STACK_TAGS))
    grades: dict[str, list[str]] = field(
        default_factory=lambda: {k: list(v) for k, v in DEFAULT_GRADE_KEYWORDS.items()}
    )
    source: Path | None = None

    @property
    def is_empty(self) -> bool:
        return not self.specializations and not self.spec_keywords


# --- загрузка ------------------------------------------------------------------------------------


def default_keywords_path() -> Path:
    from app.core.config import get_settings

    return get_settings().skills_path / KEYWORDS_FILENAME


def _as_str_list(value: Any) -> list[str]:
    if value is None or isinstance(value, dict):
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value if str(item).strip()]


def _unsupported_item(code: str, item: Any) -> UnsupportedSpec:
    if isinstance(item, dict):
        return UnsupportedSpec(
            code=code,
            keywords=tuple(_as_str_list(item.get("keywords"))),
            title=str(item.get("title") or ""),
            nearest=(str(item["nearest"]) if item.get("nearest") else None),
        )
    return UnsupportedSpec(code=code, keywords=tuple(_as_str_list(item)))


def _parse_unsupported(value: Any) -> list[UnsupportedSpec]:
    if isinstance(value, dict):
        return [_unsupported_item(str(code), item) for code, item in value.items()]
    if isinstance(value, list):
        return [
            _unsupported_item(str(item["code"]), item)
            for item in value
            if isinstance(item, dict) and item.get("code")
        ]
    return []


def parse_keywords_data(data: Any, source: Path | None = None) -> Keywords:
    """Словарь из разобранного YAML обоих форматов (чистая функция для тестов)."""
    keywords = Keywords(source=source)
    if not isinstance(data, dict):
        return keywords
    # вложенный формат: {spec: {node_id: [слова]}}
    for spec, nodes in data.items():
        if spec in _RESERVED_KEYS or not isinstance(nodes, dict):
            continue
        keywords.specializations[str(spec)] = {
            str(node_id): _as_str_list(words) for node_id, words in nodes.items()
        }
    # плоский формат: nodes: {node_id: [слова]} — специализация из префикса id
    nodes = data.get("nodes")
    if isinstance(nodes, dict):
        for node_id, words in nodes.items():
            node_id = str(node_id)
            spec = node_id.split(".", 1)[0]
            keywords.specializations.setdefault(spec, {})[node_id] = _as_str_list(words)
    specs = data.get("specializations")
    if isinstance(specs, dict):
        keywords.spec_keywords = {str(k): _as_str_list(v) for k, v in specs.items()}
    keywords.unsupported = _parse_unsupported(data.get(UNSUPPORTED_KEY))
    if data.get("stack_tags"):
        keywords.stack_tags = _as_str_list(data["stack_tags"])
    grades = data.get("grades")
    if isinstance(grades, dict):
        keywords.grades = {str(k): _as_str_list(v) for k, v in grades.items()}
    return keywords


@lru_cache(maxsize=16)
def _load_keywords_cached(path_str: str, mtime_ns: int) -> Keywords:
    path = Path(path_str)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("keywords.yaml не прочитан (%s): %s", path, exc)
        return Keywords(source=path)
    if not isinstance(data, dict):
        logger.warning("keywords.yaml: ожидался словарь, получен %s", type(data).__name__)
        return Keywords(source=path)
    return parse_keywords_data(data, path)


def load_keywords(path: Path | None = None) -> Keywords:
    """Словарь по пути (по умолчанию `settings.skills_path/keywords.yaml`); кэш по mtime.
    Отсутствующий файл — пустой словарь без исключения."""
    path = path or default_keywords_path()
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        return Keywords(source=path)
    return _load_keywords_cached(str(path), mtime_ns)


def reset_keywords_cache() -> None:
    _load_keywords_cached.cache_clear()


# --- сопоставление -----------------------------------------------------------------------------


def normalize_text(text: str) -> str:
    return (text or "").lower().replace("ё", "е")


def keyword_pattern(keyword: str) -> re.Pattern[str]:
    """Слово длиннее 3 символов без знаков — по началу слова; иначе — целиком по границам."""
    kw = normalize_text(keyword).strip()
    escaped = re.escape(kw)
    if len(kw) > 3 and _PLAIN_WORD_RE.match(kw.replace(" ", "")):
        return re.compile(rf"(?<![а-яa-z0-9]){escaped}")
    return re.compile(rf"(?<![а-яa-z0-9]){escaped}(?![а-яa-z0-9])")


@lru_cache(maxsize=4096)
def _pattern_cached(keyword: str) -> re.Pattern[str]:
    return keyword_pattern(keyword)


def find_keywords(text: str, keywords: Iterable[str]) -> tuple[str, ...]:
    """Какие ключевые слова встречаются в тексте (в порядке словаря, без повторов)."""
    haystack = normalize_text(text)
    found: list[str] = []
    for keyword in keywords:
        kw = str(keyword).strip()
        if kw and kw not in found and _pattern_cached(kw).search(haystack):
            found.append(kw)
    return tuple(found)


def match_nodes(text: str, nodes: dict[str, list[str]]) -> list[KeywordMatch]:
    matches = [
        KeywordMatch(node_id, find_keywords(text, words)) for node_id, words in nodes.items()
    ]
    return [m for m in matches if m.hits]


def detect_grade(text: str, grades: dict[str, Sequence[str]] | None = None) -> str:
    """Грейд по ключевым словам; приоритет senior > junior > middle; по умолчанию middle."""
    grades = grades or DEFAULT_GRADE_KEYWORDS
    for grade in ("senior", "junior", "middle"):
        if find_keywords(text, grades.get(grade, ())):
            return grade
    return "middle"


def detect_stack_tags(text: str, tags: Iterable[str] = DEFAULT_STACK_TAGS) -> list[str]:
    return [tag.lower() for tag in find_keywords(text, tags)]


def tokenize(text: str) -> set[str]:
    """Токены для эвристик: слова ≥ 3 символов, кириллица ≥ 6 символов усекается до
    5-символовой основы (грубый стемминг: «индексы»/«индексация» → «индек»)."""
    tokens: set[str] = set()
    for word in _WORD_RE.findall(normalize_text(text)):
        word = word.strip(".")
        if len(word) < 3:
            continue
        if re.search(r"[а-я]", word) and len(word) >= 6:
            word = word[:5]
        tokens.add(word)
    return tokens


def parse_need_by_rules(
    text: str,
    keywords: Keywords,
    *,
    specialization: str | None = None,
    max_nodes: int = 8,
) -> NeedProfile:
    """Текст потребности → NeedProfile по словарю (чистая функция, детерминированная)."""
    warnings: list[str] = []
    if keywords.is_empty:
        warnings.append("словарь ключевых слов (keywords.yaml) отсутствует или пуст")
    grade = detect_grade(text, keywords.grades)
    level = _LEVEL_BY_GRADE[grade]
    per_spec = {spec: match_nodes(text, nodes) for spec, nodes in keywords.specializations.items()}
    scores: dict[str, int] = {
        spec: sum(m.hits for m in matches) for spec, matches in per_spec.items()
    }
    for spec, words in keywords.spec_keywords.items():
        scores[spec] = scores.get(spec, 0) + len(find_keywords(text, words))
    best_score = max(scores.values(), default=0)

    chosen: str | None = specialization if specialization in keywords.specializations else None
    if specialization and chosen is None:
        warnings.append(f"специализация {specialization} не найдена в словаре")
    if chosen is None and scores:
        # при равенстве — be (как в modules/skills), иначе первая по алфавиту
        chosen = sorted(scores, key=lambda s: (-scores[s], s != "be", s))[0]
        if best_score == 0:
            warnings.append("в тексте не найдено ключевых слов ни одной специализации")
    if chosen is None:
        chosen = next(iter(keywords.specializations), "be")

    # конкурирующая поддерживаемая специализация — сигнал смешанной потребности
    for spec, score in scores.items():
        if spec != chosen and score and score >= max(1, scores.get(chosen, 0)):
            warnings.append(f"потребность затрагивает и специализацию {spec} (совпадений: {score})")

    # направление вне MVP: без явной подсказки побеждает по совпадениям → specialization: null
    unsupported_hit: UnsupportedSpec | None = None
    unsupported_found: tuple[str, ...] = ()
    for item in keywords.unsupported:
        found = find_keywords(text, item.keywords)
        if found and len(found) > len(unsupported_found):
            unsupported_hit, unsupported_found = item, found
    specialization_out: str | None = chosen
    if (
        unsupported_hit is not None
        and specialization is None
        and len(unsupported_found) >= max(1, scores.get(chosen, 0))
    ):
        nearest = unsupported_hit.nearest or chosen
        title = unsupported_hit.title or unsupported_hit.code
        warnings.append(f"{WARNING_NOT_SUPPORTED}:{unsupported_hit.code}")
        warnings.append(
            f"направление «{title}» пока не поддерживается тестом (совпадения: "
            + ", ".join(unsupported_found)
            + f"); ближайшая специализация — {nearest}"
        )
        specialization_out = None
        chosen = nearest

    matches = sorted(per_spec.get(chosen, []), key=lambda m: (-m.hits, m.node_id))[:max_nodes]
    max_hits = max((m.hits for m in matches), default=1)
    nodes = [
        NeedNode(
            id=m.node_id,
            min_level=level,
            importance=round(0.5 + 0.5 * m.hits / max_hits, 2),
            evidence="ключевые слова: " + ", ".join(m.keywords),
        )
        for m in matches
    ]
    return NeedProfile(
        specialization=specialization_out,
        grade=grade,  # type: ignore[arg-type]
        nodes=nodes if specialization_out is not None else [],
        stack_tags=detect_stack_tags(text, keywords.stack_tags),
        nice_to_have=[],
        warnings=warnings,
    )


# --- провайдер -----------------------------------------------------------------------------------


class RulesProvider(LLMProvider):
    """`LLM_PROVIDER=rules`: need_parse по словарю, остальное — не поддерживается."""

    name = "rules"
    SUPPORTED = frozenset({"need_parse"})

    def __init__(self, keywords_path: Path | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._keywords_path = keywords_path

    @property
    def keywords(self) -> Keywords:
        return load_keywords(self._keywords_path)

    def model_for(self, tier: str) -> str:
        return "rules-keywords"

    def supports(self, prompt_name: str) -> bool:
        return prompt_name in self.SUPPORTED

    async def _complete(self, messages, *, temperature, schema, model) -> RawCompletion:
        raise NotImplementedError("RulesProvider строит ответ из переменных, не из сообщений")

    async def complete(
        self,
        prompt: PromptSpec,
        variables: dict[str, Any],
        *,
        schema: type[BaseModel] | None = None,
        temperature: float | None = None,
    ) -> CompletionResult:
        if not self.supports(prompt.name):
            raise LLMUnsupportedError(
                f"rules: промпт {prompt.name} не поддерживается (только need_parse)"
            )
        text = str(variables.get("need_text") or variables.get("text") or "")
        hint = variables.get("specialization")
        profile = parse_need_by_rules(
            text, self.keywords, specialization=str(hint) if hint else None
        )
        profile.warnings.insert(0, "режим без LLM: разбор по словарю ключевых слов")
        parsed = (schema or prompt.schema).model_validate(profile.model_dump())
        return CompletionResult(
            parsed=parsed,
            provider=self.name,
            model=self.model_for(prompt.tier),
            prompt=prompt,
            attempts=1,
            latency_ms=0,
            raw_text=parsed.model_dump_json(),
        )


__all__ = [
    "DEFAULT_GRADE_KEYWORDS",
    "DEFAULT_STACK_TAGS",
    "KEYWORDS_FILENAME",
    "WARNING_NOT_SUPPORTED",
    "KeywordMatch",
    "Keywords",
    "RulesProvider",
    "UnsupportedSpec",
    "default_keywords_path",
    "detect_grade",
    "detect_stack_tags",
    "find_keywords",
    "keyword_pattern",
    "load_keywords",
    "match_nodes",
    "normalize_text",
    "parse_keywords_data",
    "parse_need_by_rules",
    "reset_keywords_cache",
    "tokenize",
]
