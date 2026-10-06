"""Модуль assessment: чистая функция `compute_integrity` — каждый флаг, границы
порогов и отображение числа флагов в уровень high/medium/low."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

# модель под алиасом: имя Test* pytest пытается собрать как тест-класс
from app.models.assessment import TestSessionEvent as SessionEvent
from app.modules.assessment.config import IntegritySettings
from app.modules.assessment.integrity import compute_integrity

T0 = datetime(2026, 10, 8, 10, 0, 0, tzinfo=UTC)

SETTINGS = IntegritySettings(
    integrity_paste_len=200,
    integrity_blur_seconds=5,
    integrity_min_seconds_per_item=20,
    integrity_no_edit_min_len=120,
)


def ev(kind: str, offset_s: float = 0, **payload: Any) -> SessionEvent:
    return SessionEvent(kind=kind, client_ts=T0 + timedelta(seconds=offset_s), payload=payload)


def flag_codes(events: list[SessionEvent]) -> list[str]:
    return [f.code for f in compute_integrity(events, SETTINGS).flags]


class TestLargePaste:
    def test_at_threshold_flagged(self) -> None:
        assert flag_codes([ev("paste", 1, len=200)]) == ["large_paste"]

    def test_below_threshold_clean(self) -> None:
        assert flag_codes([ev("paste", 1, len=199)]) == []

    def test_chars_pasted_fallback(self) -> None:
        assert flag_codes([ev("paste", 1, chars_pasted=250)]) == ["large_paste"]

    def test_counts_and_detail(self) -> None:
        result = compute_integrity(
            [ev("paste", 1, len=300), ev("paste", 2, len=500), ev("paste", 3, len=10)], SETTINGS
        )
        flag = result.flags[0]
        assert (flag.code, flag.count) == ("large_paste", 2)
        assert "500" in flag.detail

    def test_paste_without_size_ignored(self) -> None:
        assert flag_codes([ev("paste", 1)]) == []


class TestTabBlur:
    def test_gap_at_threshold_flagged(self) -> None:
        assert flag_codes([ev("blur", 0), ev("focus", 5)]) == ["tab_blur"]

    def test_short_gap_clean(self) -> None:
        assert flag_codes([ev("blur", 0), ev("focus", 4.9)]) == []

    def test_trailing_blur_counts(self) -> None:
        assert flag_codes([ev("blur", 0)]) == ["tab_blur"]

    def test_multiple_gaps_counted(self) -> None:
        events = [ev("blur", 0), ev("focus", 10), ev("blur", 20), ev("focus", 40)]
        result = compute_integrity(events, SETTINGS)
        assert result.flags[0].count == 2

    def test_unsorted_events_are_ordered_by_client_ts(self) -> None:
        # события приходят батчами и могут быть перемешаны — сортировка внутри функции
        assert flag_codes([ev("focus", 30), ev("blur", 0)]) == ["tab_blur"]


class TestFastAnswer:
    def test_duration_ms_below_threshold_flagged(self) -> None:
        assert flag_codes([ev("answer_submitted", 30, item_id="i1", duration_ms=19_999)]) == [
            "fast_answer"
        ]

    def test_duration_ms_at_threshold_clean(self) -> None:
        assert flag_codes([ev("answer_submitted", 30, item_id="i1", duration_ms=20_000)]) == []

    def test_timestamps_fallback(self) -> None:
        events = [ev("item_shown", 0, item_id="i1"), ev("answer_submitted", 10, item_id="i1")]
        assert flag_codes(events) == ["fast_answer"]

    def test_timestamps_fallback_slow_clean(self) -> None:
        events = [ev("item_shown", 0, item_id="i1"), ev("answer_submitted", 25, item_id="i1")]
        assert flag_codes(events) == []

    def test_no_item_id_ignored(self) -> None:
        assert flag_codes([ev("answer_submitted", 1, duration_ms=1_000)]) == []


class TestNoEdit:
    def test_long_answer_without_typing_flagged(self) -> None:
        assert flag_codes(
            [ev("answer_submitted", 30, item_id="i1", len=120, chars_typed=0, duration_ms=30_000)]
        ) == ["no_edit"]

    def test_short_answer_clean(self) -> None:
        assert (
            flag_codes(
                [
                    ev(
                        "answer_submitted",
                        30,
                        item_id="i1",
                        len=119,
                        chars_typed=0,
                        duration_ms=30_000,
                    )
                ]
            )
            == []
        )

    def test_typed_answer_clean(self) -> None:
        assert (
            flag_codes(
                [
                    ev(
                        "answer_submitted",
                        30,
                        item_id="i1",
                        len=400,
                        chars_typed=1,
                        duration_ms=30_000,
                    )
                ]
            )
            == []
        )

    def test_missing_chars_typed_not_flagged(self) -> None:
        # нет данных о наборе — не флагуем (честность формулы)
        assert (
            flag_codes([ev("answer_submitted", 30, item_id="i1", len=400, duration_ms=30_000)])
            == []
        )


class TestLevels:
    def test_no_events_high(self) -> None:
        result = compute_integrity([], SETTINGS)
        assert (result.level, result.flags, result.events_total) == ("high", [], 0)

    def test_zero_flags_high(self) -> None:
        events = [
            ev("item_shown", 0, item_id="i1"),
            ev("first_input", 10, item_id="i1"),
            ev("answer_submitted", 60, item_id="i1", len=200, chars_typed=180, duration_ms=60_000),
        ]
        result = compute_integrity(events, SETTINGS)
        assert result.level == "high"
        assert result.events_total == 3

    def test_one_flag_medium(self) -> None:
        assert compute_integrity([ev("paste", 1, len=300)], SETTINGS).level == "medium"

    def test_two_flags_medium(self) -> None:
        events = [ev("paste", 1, len=300), ev("blur", 2), ev("focus", 20)]
        assert compute_integrity(events, SETTINGS).level == "medium"

    def test_three_flags_low(self) -> None:
        events = [
            ev("paste", 1, len=300),
            ev("blur", 2),
            ev("focus", 20),
            ev("answer_submitted", 25, item_id="i1", duration_ms=5_000),
        ]
        assert compute_integrity(events, SETTINGS).level == "low"

    def test_repeats_of_one_flag_still_medium(self) -> None:
        # считаются виды флагов, не повторы: 5 крупных вставок — один флаг
        events = [ev("paste", i, len=300) for i in range(5)]
        result = compute_integrity(events, SETTINGS)
        assert result.level == "medium"
        assert result.flags[0].count == 5
