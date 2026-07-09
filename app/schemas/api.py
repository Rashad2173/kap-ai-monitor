from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class AskRequest(BaseModel):
    bank: str = "ALL"
    question: str


class AskResponse(BaseModel):
    answer: str
    mode: str
    sources: list[dict[str, Any]]


class DashboardSummaryResponse(BaseModel):
    profit: dict[str, Any]
    highlight: dict[str, Any]
    audit: dict[str, Any]
    summary: str
    period_label: str = ""