from __future__ import annotations

from openai import OpenAI

from app.core.config import settings


_openai_client: OpenAI | None = None


def get_openai_client() -> OpenAI:
    global _openai_client

    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY .env dosyasında bulunamadı.")

    if _openai_client is None:
        _openai_client = OpenAI(api_key=settings.OPENAI_API_KEY)

    return _openai_client


def get_openai_model() -> str:
    return settings.OPENAI_MODEL