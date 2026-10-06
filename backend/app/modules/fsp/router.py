"""Роутер модуля `fsp` — заглушка каркаса; эндпоинты добавляет владелец (PLAN.md §7.5)."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/fsp", tags=["fsp"])
