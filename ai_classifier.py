from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, ValidationError

from config import get_settings
from models import (
    AIQualificationResult,
    EstimatedBudget,
    GeminiLeadScoreSchema,
    LeadApprovalStatus,
)

logger = logging.getLogger(__name__)

GEMINI_MODEL_FALLBACKS: tuple[str, ...] = (
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-flash-latest",
)

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

SYSTEM_PROMPT = """You are an expert lead-generation and first-pass scoring assistant for inbound freelance, Reddit, Hacker News, Twitter (X), and Xiaohongshu posts.

Goal: Decide whether a lead fits a premium full-stack developer profile with typical project checks from $1,500+.

## HIGH-PRIORITY FIT (boost score)
Stack & product:
- Next.js, React, JavaScript, Python, Supabase
- Premium web design, Figma-to-Code, custom UI, Glassmorphism, Y2K UI, Cyber-minimalism
- End-to-end MVP builds, early-stage startups, SaaS redesigns, creative development

High-value keyword signals (not required alone, but strong positive):
- EN: "looking for founding developer", "need fullstack mvp", "saas redesign", "design to code figma", "high end web design", "aesthetic website dev", "nextjs supabase developer", "hiring custom ui dev", "creative developer"
- DE: "webdesign gesucht", "fullstack entwickler", "mvp erstellen"
- ZH: "全栈开发", "独立开发者", "高端网页设计", "MVP开发"

## HARD REJECT (status=Rejected, score usually below 40)
Always reject if the author is a freelancer/service provider advertising themselves, a job seeker, or spam.

Also reject low-ticket / routine work:
- CMS-only builds: WordPress, Tilda, Webflow, Shopify (unless clearly complex headless/custom)
- Small bugfixes: "fix website layout", "site is down", database/server migration, quick patches
- Low-budget markers: explicit budget under $1,000, "simple task", "quick fix", micro-gigs

## SCORING (0-100)
- 80-100: Ideal — premium stack, MVP/SaaS/custom UI, founding-dev or high-end design-to-code, budget likely $1,500+
- 60-79: Good fit — relevant stack and scope, some premium signals, budget unclear or medium
- 40-59: Weak — vague web need, mixed signals, or medium-low scope
- 0-39: Reject — wrong author type, exclusion criteria, or clearly low-budget/routine

## APPROVAL RULE
- status=Approved ONLY if: genuine client/company hiring, NOT excluded category, score >= 60, and estimated_budget is NOT Low
- status=Rejected otherwise

## estimated_budget — use EXACTLY one of: High, Medium, Low, Unknown
## status — use EXACTLY one of: Approved, Rejected

Return JSON with these keys only:
status, score, estimated_budget, summary, why_it_fits
why_it_fits must be in Russian.
"""


class _FlexibleGeminiPayload(BaseModel):
    """Accept Gemini variants / legacy keys before normalizing."""

    model_config = ConfigDict(extra="ignore")

    status: Optional[str] = None
    score: Optional[int] = None
    estimated_budget: Optional[str] = None
    summary: Optional[str] = None
    why_it_fits: Optional[str] = None
    reason: Optional[str] = None
    is_lead: Optional[bool] = None


def _strip_json_fences(raw: str) -> str:
    return _JSON_FENCE_RE.sub("", raw.strip()).strip()


def _normalize_status(value: Optional[str], *, is_lead: Optional[bool]) -> LeadApprovalStatus:
    if value:
        normalized = value.strip().lower()
        if normalized in {"approved", "approve", "yes", "true"}:
            return LeadApprovalStatus.APPROVED
        if normalized in {"rejected", "reject", "no", "false"}:
            return LeadApprovalStatus.REJECTED
    if is_lead is True:
        return LeadApprovalStatus.APPROVED
    if is_lead is False:
        return LeadApprovalStatus.REJECTED
    return LeadApprovalStatus.REJECTED


def _normalize_budget(value: Optional[str]) -> EstimatedBudget:
    if not value:
        return EstimatedBudget.UNKNOWN
    normalized = value.strip().lower()
    if normalized.startswith("high"):
        return EstimatedBudget.HIGH
    if normalized.startswith("medium") or normalized.startswith("med"):
        return EstimatedBudget.MEDIUM
    if normalized.startswith("low"):
        return EstimatedBudget.LOW
    if "unknown" in normalized or normalized in {"?", "n/a", "na"}:
        return EstimatedBudget.UNKNOWN
    return EstimatedBudget.UNKNOWN


def _clamp_score(value: Any) -> int:
    try:
        score = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, score))


def _extract_json_object(raw: str) -> str:
    cleaned = _strip_json_fences(raw)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        return cleaned[start : end + 1]
    return cleaned


def _parse_response(raw: str) -> AIQualificationResult:
    cleaned = _extract_json_object(raw)
    data = json.loads(cleaned)

    if isinstance(data, list) and data and isinstance(data[0], dict):
        data = data[0]

    if not isinstance(data, dict):
        raise ValidationError.from_exception_data(
            "GeminiPayload",
            [{"type": "dict_type", "loc": ("root",), "input": data}],
        )

    try:
        schema = GeminiLeadScoreSchema.model_validate(data)
        return AIQualificationResult.model_validate(schema.model_dump())
    except ValidationError:
        pass

    flex = _FlexibleGeminiPayload.model_validate(data)
    why = (flex.why_it_fits or flex.reason or "").strip()
    if not why:
        why = "Ответ Gemini без поля why_it_fits/reason"

    status = _normalize_status(flex.status, is_lead=flex.is_lead)
    score = _clamp_score(flex.score)
    if flex.is_lead is True and score < 60:
        score = 65
    if flex.is_lead is False and score >= 60:
        score = min(score, 45)

    return AIQualificationResult(
        status=status,
        score=score,
        estimated_budget=_normalize_budget(flex.estimated_budget),
        summary=(flex.summary or "").strip() or None,
        why_it_fits=why,
    )


def _build_client(api_key: str):
    from google import genai

    return genai.Client(api_key=api_key)


def _generate_sync(client, model: str, text: str, *, use_schema: bool = True) -> str:
    from google.genai import types

    config_kwargs: dict = {
        "system_instruction": SYSTEM_PROMPT,
        "temperature": 0.1,
        "max_output_tokens": 512,
        "response_mime_type": "application/json",
    }
    if use_schema:
        config_kwargs["response_schema"] = GeminiLeadScoreSchema

    response = client.models.generate_content(
        model=model,
        contents=text,
        config=types.GenerateContentConfig(**config_kwargs),
    )
    return response.text or "{}"


def _generate_sync_plain(client, model: str, text: str) -> str:
    return _generate_sync(client, model, text, use_schema=False)


def _models_to_try(primary: str) -> list[str]:
    ordered: list[str] = []
    for name in (primary, *GEMINI_MODEL_FALLBACKS):
        if name and name not in ordered:
            ordered.append(name)
    return ordered


def _is_parse_error(result: AIQualificationResult) -> bool:
    return result.why_it_fits == "Некорректный structured output от Gemini"


async def _call_and_parse(
    loop: asyncio.AbstractEventLoop,
    client,
    model_name: str,
    text: str,
    *,
    use_schema: bool,
) -> tuple[AIQualificationResult, str]:
    generate = _generate_sync if use_schema else _generate_sync_plain
    raw = await loop.run_in_executor(None, generate, client, model_name, text)
    try:
        return _parse_response(raw), raw
    except (json.JSONDecodeError, ValidationError) as exc:
        logger.error(
            "Invalid Gemini JSON (%s schema=%s): %s — raw: %s",
            model_name,
            use_schema,
            exc,
            raw[:500],
        )
        return _error_result("Некорректный structured output от Gemini"), raw


def _error_result(message: str) -> AIQualificationResult:
    return AIQualificationResult(
        status=LeadApprovalStatus.REJECTED,
        score=0,
        estimated_budget=EstimatedBudget.UNKNOWN,
        summary=None,
        why_it_fits=message,
    )


async def qualify_lead(text: str) -> AIQualificationResult:
    """Score post via Gemini structured JSON → AIQualificationResult."""
    settings = get_settings()

    if not settings.gemini_api_key:
        logger.warning("GEMINI_API_KEY not set")
        return _error_result("API-ключ Gemini не настроен")

    client = _build_client(settings.gemini_api_key)
    loop = asyncio.get_running_loop()
    last_exc: Exception | None = None
    result = _error_result("Ошибка Gemini API")
    raw = ""
    parsed_ok = False

    for model_name in _models_to_try(settings.gemini_model):
        try:
            result, raw = await _call_and_parse(
                loop, client, model_name, text, use_schema=True
            )
            if model_name != settings.gemini_model:
                logger.info("Gemini fallback model used: %s", model_name)

            if _is_parse_error(result):
                logger.warning(
                    "Structured schema parse failed on %s — retry plain JSON",
                    model_name,
                )
                result, raw = await _call_and_parse(
                    loop, client, model_name, text, use_schema=False
                )

            if not _is_parse_error(result):
                parsed_ok = True
                break
        except Exception as exc:
            last_exc = exc
            err = str(exc).lower()
            if "404" in err or "not found" in err:
                logger.warning("Gemini model %s unavailable: %s", model_name, exc)
                continue
            logger.exception("Gemini API error: %s", exc)
            return _error_result(f"Ошибка Gemini API: {exc}")

    if not parsed_ok:
        if last_exc:
            logger.error("All Gemini models failed: %s", last_exc)
            return _error_result(f"Ошибка Gemini API: {last_exc}")
        logger.error("Gemini parse failed after retries — raw: %s", raw[:500])
        return result

    if result.is_lead and not result.summary:
        result.summary = result.why_it_fits

    logger.info(
        "AI score: %s | %d | budget=%s — %s",
        result.status.value,
        result.score,
        result.estimated_budget.value,
        result.why_it_fits[:80],
    )
    return result
