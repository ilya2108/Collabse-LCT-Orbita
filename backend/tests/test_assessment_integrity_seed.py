"""Демо-сид сигналов целостности: сценарии телеметрии дают ровно те уровни
high/medium/low, которые жюри увидит бейджами в выдаче работодателя."""

from __future__ import annotations

from datetime import UTC, datetime

# модель под алиасом: имя Test* pytest пытается собрать как тест-класс
from app.models.assessment import TestSessionEvent as SessionEvent
from app.modules.assessment.config import IntegritySettings
from app.modules.assessment.integrity import compute_integrity
from app.modules.assessment.seed import DEMO_SCENARIOS, _events_for

SETTINGS = IntegritySettings(
    integrity_paste_len=200,
    integrity_blur_seconds=5,
    integrity_min_seconds_per_item=20,
    integrity_no_edit_min_len=120,
)
T0 = datetime(2026, 10, 6, 12, 0, 0, tzinfo=UTC)


def test_scenarios_cover_all_levels() -> None:
    assert [scenario for _, scenario in DEMO_SCENARIOS] == ["high", "medium", "low"]


def test_scenario_events_produce_declared_levels() -> None:
    for _, scenario in DEMO_SCENARIOS:
        events = [SessionEvent(**spec) for spec in _events_for(scenario, T0)]
        result = compute_integrity(events, SETTINGS)
        assert result.level == scenario, f"сценарий {scenario} дал {result.level}"
        assert result.events_total == len(events)


def test_payloads_contain_no_answer_texts() -> None:
    allowed = {"item_id", "len", "duration_ms", "chars_typed", "chars_pasted"}
    for _, scenario in DEMO_SCENARIOS:
        for spec in _events_for(scenario, T0):
            assert set(spec["payload"]) <= allowed
