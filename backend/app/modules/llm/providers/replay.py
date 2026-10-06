"""Провайдер `replay`: ответы LLM из кэша в репозитории `backend/seed/llm_cache.jsonl`.

Жюри запускает решение без ключей и получает те же разборы потребностей и оценки
демо-ответов, что и на стенде (PLAN.md §6). Файл собирает `make llm-freeze` из журнала
`llm_call` (`journal.export_replay`); каждая строка — JSON:

    {"hash": "<sha256 kind+version+model+вход>", "input_hash": "<sha256 входа>",
     "kind": "need_parse", "prompt_version": "v1", "model": "GigaChat-2-Pro",
     "response": {...}, "tokens_in": 812, "tokens_out": 203}

Поиск — по (kind, prompt_version, input_hash): модель, которой получен ответ, не важна,
важно, что вход совпадает после санитайзера. Нет записи → `LLMReplayMissError` с понятным
текстом (какой промпт, какой хэш, что делать); фолбэка на stub у replay нет намеренно —
молчаливая подмена скрыла бы неполный кэш.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from app.modules.llm.base import CompletionResult, LLMProvider, LLMUnsupportedError, RawCompletion
from app.modules.llm.journal import canonical_json, input_hash
from app.modules.llm.prompts import PromptSpec

logger = logging.getLogger("app.llm.replay")

REPLAY_FILENAME = "llm_cache.jsonl"
REPLAY_PATH_ENV = "LLM_REPLAY_PATH"


class LLMReplayMissError(LLMUnsupportedError):
    """В кэше replay нет ответа на этот вход."""


@dataclass(frozen=True)
class ReplayRecord:
    kind: str
    prompt_version: str
    input_hash: str
    model: str
    response: dict[str, Any]
    tokens_in: int = 0
    tokens_out: int = 0
    hash: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.kind, self.prompt_version, self.input_hash)

    def to_json(self) -> dict[str, Any]:
        return {
            "hash": self.hash,
            "input_hash": self.input_hash,
            "kind": self.kind,
            "prompt_version": self.prompt_version,
            "model": self.model,
            "response": self.response,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
        }


def default_replay_path() -> Path:
    """`LLM_REPLAY_PATH` из окружения или `backend/seed/llm_cache.jsonl`."""
    override = os.environ.get(REPLAY_PATH_ENV)
    if override:
        return Path(override)
    from app.core.config import get_settings

    return get_settings().backend_root / "seed" / REPLAY_FILENAME


def _record_from_json(data: dict[str, Any]) -> ReplayRecord | None:
    try:
        return ReplayRecord(
            kind=str(data["kind"]),
            prompt_version=str(data["prompt_version"]),
            input_hash=str(data["input_hash"]),
            model=str(data.get("model") or "replay"),
            response=dict(data["response"]),
            tokens_in=int(data.get("tokens_in") or 0),
            tokens_out=int(data.get("tokens_out") or 0),
            hash=str(data.get("hash") or ""),
        )
    except (KeyError, TypeError, ValueError):
        return None


@lru_cache(maxsize=4)
def _load_index(path_str: str, mtime_ns: int) -> dict[tuple[str, str, str], ReplayRecord]:
    index: dict[tuple[str, str, str], ReplayRecord] = {}
    path = Path(path_str)
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except ValueError:
                logger.warning("llm_cache.jsonl:%d — не JSON, строка пропущена", line_no)
                continue
            record = _record_from_json(data) if isinstance(data, dict) else None
            if record is None:
                logger.warning("llm_cache.jsonl:%d — неполная запись, пропущена", line_no)
                continue
            index[record.key] = record  # последняя запись по ключу побеждает
    return index


def load_replay_index(path: Path | None = None) -> dict[tuple[str, str, str], ReplayRecord]:
    """Индекс кэша (кэшируется по mtime файла); отсутствующий файл — пустой индекс."""
    path = path or default_replay_path()
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        return {}
    return _load_index(str(path), mtime_ns)


def reset_replay_cache() -> None:
    _load_index.cache_clear()


def append_replay_record(path: Path, record: ReplayRecord) -> None:
    """Дописать запись в jsonl (для `llm_freeze`); каталог создаётся при необходимости."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(canonical_json(record.to_json()) + "\n")
    reset_replay_cache()


class ReplayProvider(LLMProvider):
    """`LLM_PROVIDER=replay`: ответы только из кэша, без сети и без stub."""

    name = "replay"

    def __init__(self, path: Path | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._path = path

    @property
    def path(self) -> Path:
        return self._path or default_replay_path()

    def model_for(self, tier: str) -> str:
        return "replay"

    async def _complete(self, messages, *, temperature, schema, model) -> RawCompletion:
        raise NotImplementedError("ReplayProvider ищет ответ по входу, не по сообщениям")

    def lookup(self, prompt: PromptSpec, variables: dict[str, Any]) -> ReplayRecord:
        key = (prompt.name, prompt.version, input_hash(variables))
        record = load_replay_index(self.path).get(key)
        if record is None:
            raise LLMReplayMissError(
                f"replay: для промпта {prompt.full_name} нет ответа в кэше {self.path} "
                f"(input_hash {key[2][:12]}…). Соберите кэш командой `make llm-freeze` при "
                "наличии ключа провайдера или запустите с LLM_PROVIDER=stub"
            )
        return record

    async def complete(
        self,
        prompt: PromptSpec,
        variables: dict[str, Any],
        *,
        schema: type[BaseModel] | None = None,
        temperature: float | None = None,
    ) -> CompletionResult:
        started = time.perf_counter()
        record = self.lookup(prompt, variables)
        schema = schema or prompt.schema
        try:
            parsed = schema.model_validate(record.response)
        except ValidationError as exc:
            raise LLMReplayMissError(
                f"replay: запись для {prompt.full_name} не соответствует схеме "
                f"{schema.__name__}: {exc.error_count()} ошибок — пересоберите кэш"
            ) from exc
        return CompletionResult(
            parsed=parsed,
            provider=self.name,
            model=record.model,
            prompt=prompt,
            attempts=1,
            tokens_in=record.tokens_in,
            tokens_out=record.tokens_out,
            latency_ms=int((time.perf_counter() - started) * 1000),
            raw_text=parsed.model_dump_json(),
            prompt_hash=record.hash,
        )


__all__ = [
    "REPLAY_FILENAME",
    "REPLAY_PATH_ENV",
    "LLMReplayMissError",
    "ReplayProvider",
    "ReplayRecord",
    "append_replay_record",
    "default_replay_path",
    "load_replay_index",
    "reset_replay_cache",
]
