from __future__ import annotations

import asyncio
import json
import logging

from pydantic import ValidationError

from config import get_settings
from models import AIQualificationResult

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Ты — AI-валидатор лидов для веб-студии. Анализируй текст. "
    "Твоя задача — определить, является ли автор сообщения ЗАКАЗЧИКОМ (клиентом), "
    "которому нужен сайт, веб-дизайн, MVP или веб-разработчик/дизайнер. "
    "Компания, публикующая вакансию на веб-дизайнера, frontend или UI/UX — is_lead=true. "
    "Если автор сам рекламирует услуги или ищет работу в штат (соискатель) — is_lead=false. "
    "Поле reason — краткое объяснение на русском. "
    "Поле summary — суть задачи одним предложением (если is_lead=true, иначе null)."
)


def _build_client(api_key: str):
    from google import genai

    return genai.Client(api_key=api_key)


def _generate_sync(client, model: str, text: str) -> str:
    """Synchronous Gemini call with structured JSON (Pydantic schema)."""
    from google.genai import types

    response = client.models.generate_content(
        model=model,
        contents=text,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.1,
            max_output_tokens=300,
            response_mime_type="application/json",
            response_schema=AIQualificationResult,
        ),
    )
    return response.text or "{}"


async def qualify_lead(text: str) -> AIQualificationResult:
    """Classify post intent via Gemini (structured JSON → AIQualificationResult)."""
    settings = get_settings()

    if not settings.gemini_api_key:
        logger.warning("GEMINI_API_KEY not set")
        return AIQualificationResult(
            is_lead=False,
            reason="API-ключ Gemini не настроен",
            summary=None,
        )

    client = _build_client(settings.gemini_api_key)

    try:
        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(
            None,
            _generate_sync,
            client,
            settings.gemini_model,
            text,
        )
    except Exception as exc:
        logger.exception("Gemini API error: %s", exc)
        return AIQualificationResult(
            is_lead=False,
            reason=f"Ошибка Gemini API: {exc}",
            summary=None,
        )

    try:
        result = AIQualificationResult.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        logger.error("Invalid Gemini JSON: %s — raw: %s", exc, raw)
        try:
            result = AIQualificationResult.model_validate_json(raw)
        except ValidationError:
            return AIQualificationResult(
                is_lead=False,
                reason="Некорректный structured output от Gemini",
                summary=None,
            )

    if result.is_lead and not result.summary:
        result.summary = result.reason

    return result
