from __future__ import annotations

import json
import logging
from typing import Any

from app.integrations.openai_client import get_openai_client, get_openai_model

logger = logging.getLogger(__name__)


def safe_chat_completion(
    messages: list[dict[str, Any]],
    *,
    temperature: float = 0,
    response_format: dict[str, Any] | None = None,
    default: str | None = None,
) -> str | None:
    """OpenAI chat completion çağrısını hata yönetimiyle sarar.

    Başarılıysa mesaj içeriğini (str) döndürür. Ağ hatası, zaman aşımı,
    rate-limit veya başka bir API hatasında çökmek yerine ``default`` döner.
    Böylece çağıran servis deterministik fallback'ine düşebilir.
    """
    try:
        client = get_openai_client()

        kwargs: dict[str, Any] = {
            "model": get_openai_model(),
            "temperature": temperature,
            "messages": messages,
        }

        if response_format is not None:
            kwargs["response_format"] = response_format

        response = client.chat.completions.create(**kwargs)

        content = response.choices[0].message.content

        return content if content else default

    except Exception as exc:  # noqa: BLE001 - her API hatasında fallback istiyoruz
        logger.warning("OpenAI chat completion başarısız: %s", exc)
        return default


def safe_json_completion(
    messages: list[dict[str, Any]],
    *,
    temperature: float = 0,
    default: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """JSON modunda completion çağırır ve parse eder.

    API hatası ya da geçersiz JSON durumunda ``default`` döndürür.
    """
    content = safe_chat_completion(
        messages,
        temperature=temperature,
        response_format={"type": "json_object"},
        default=None,
    )

    if content is None:
        return default

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        logger.warning("OpenAI yanıtı JSON olarak parse edilemedi.")
        return default

    if not isinstance(data, dict):
        return default

    return data