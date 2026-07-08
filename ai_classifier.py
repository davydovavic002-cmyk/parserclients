from __future__ import annotations

import asyncio
import json
import logging
import re
from functools import partial
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

MAX_LEAD_INPUT_CHARS = 6000

# gemini-2.0-flash retired — do not use
GEMINI_MODEL_FALLBACKS: tuple[str, ...] = (
    "gemini-2.5-flash",
    "gemini-flash-latest",
    "gemini-2.5-flash-lite",
)

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_STATUS_RE = re.compile(r'"status"\s*:\s*"([^"]+)"', re.IGNORECASE)
_SCORE_RE = re.compile(r'"score"\s*:\s*(\d+)')
_BUDGET_RE = re.compile(r'"estimated_budget"\s*:\s*"([^"]*)"', re.IGNORECASE)
_SUMMARY_RE = re.compile(r'"summary"\s*:\s*"(.*?)(?:"|$)', re.DOTALL)
_WHY_RE = re.compile(r'"why_it_fits"\s*:\s*"(.*?)(?:"|$)', re.DOTALL)
_REASON_RE = re.compile(r'"reason"\s*:\s*"(.*?)(?:"|$)', re.DOTALL)

SYSTEM_PROMPT = """You are a lead scorer for a freelance web DESIGNER + FULLSTACK DEVELOPER who takes PROJECT-BASED CUSTOM CODE work ($500+), not full-time employment.

## APPROVE — project clients (design OR fullstack, equal priority):
Niches: lifestyle, fashion, food, music, wellness, health, sports, education, e-commerce, crypto/web3, DTC/boutique, indie/cool projects.

Design projects: brand website, landing page, redesign, Figma-to-code, custom UI for web apps.
Fullstack projects: MVP/web app build, Next.js/React/Supabase stack, SaaS prototype, API+frontend contract, founder needs dev for launch — must be freelance/contract/one-off, NOT staff hire.

## HARD REJECT (score 0-35):
1) Corporate full-time jobs (full-time, permanent, join our team, salary+benefits, visa, senior staff role at enterprise)
2) Job seekers / spam / service providers advertising themselves (for hire, my portfolio)
3) Clearly tiny gigs under $300 (logo for $50, 1-hour fix)
4) CMS / no-code builder ONLY: WordPress, Tilda, Webflow, Wix, Squarespace, Bitrix, Elementor, Joomla — UNLESS custom React/Next.js/fullstack is also required

## PROJECT vs JOB:
APPROVE = client needs a finite WEBSITE/WEB APP PROJECT with custom development or Figma-to-code (design-only OR fullstack).
REJECT = hiring an employee; REJECT = CMS-only build with no custom stack.

## SCORING
- 75-100: clear project + niche brand + design OR fullstack scope
- 50-74: solid freelance project, stack/niche fit — APPROVE if real client project
- 0-49: reject

## APPROVAL: score >= 50, NOT corporate FT, NOT CMS-only.
If budget is unclear — use estimated_budget=Unknown and APPROVE when scope fits.
NEVER reject only because budget is not stated. Unknown budget is always acceptable.

## OUTBOUND — US local business (Google Maps, no website):
APPROVE if independent local business in lifestyle/fashion/food/wellness/beauty/fitness — good prospect for brand website ($500+).
REJECT big chains/franchises, businesses that already have a professional custom-domain site, or pure menu-only needs.
Score 55-75 for solid local prospect; 75+ if clear premium/lifestyle brand fit.

## OUTBOUND — entrepreneur posts (starting business, no website yet):
APPROVE if US/EN founder/small business actively launching and needs web presence — treat as warm prospect.
REJECT vague advice threads with no buying intent.

## estimated_budget: High ($1200+), Medium ($500-$1200), Low (<$500), Unknown

## OUTPUT JSON: status, score, estimated_budget, summary (max 100 chars), why_it_fits (max 80 chars, Russian)
"""


class _FlexibleGeminiPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Optional[str] = None
    score: Optional[int] = None
    estimated_budget: Optional[str] = None
    summary: Optional[str] = None
    why_it_fits: Optional[str] = None
    reason: Optional[str] = None
    is_lead: Optional[bool] = None


def is_gemini_failure(result: AIQualificationResult) -> bool:
    """True when result must not be saved — retry later."""
    msg = result.why_it_fits
    return msg.startswith(
        (
            "Некорректный structured output",
            "Ошибка Gemini API",
            "API-ключ Gemini",
        )
    )


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


def _looks_truncated(raw: str) -> bool:
    cleaned = _strip_json_fences(raw).strip()
    if not cleaned:
        return True
    if not cleaned.endswith("}"):
        return True
    try:
        json.loads(_extract_json_object(raw))
        return False
    except json.JSONDecodeError:
        return True


def _salvage_partial_json(raw: str) -> Optional[AIQualificationResult]:
    """Extract fields from truncated Gemini JSON."""
    if "{" not in raw:
        return None

    status_m = _STATUS_RE.search(raw)
    score_m = _SCORE_RE.search(raw)
    if not status_m and not score_m:
        return None

    budget_m = _BUDGET_RE.search(raw)
    summary_m = _SUMMARY_RE.search(raw)
    why_m = _WHY_RE.search(raw) or _REASON_RE.search(raw)

    why = (why_m.group(1).strip() if why_m else "")[:500]
    if not why and summary_m:
        why = summary_m.group(1).strip()[:500]
    if not why:
        why = "Частичный ответ Gemini (JSON обрезан)"

    summary = (summary_m.group(1).strip() if summary_m else "")[:300] or None

    return AIQualificationResult(
        status=_normalize_status(
            status_m.group(1) if status_m else None,
            is_lead=None,
        ),
        score=_clamp_score(score_m.group(1) if score_m else 0),
        estimated_budget=_normalize_budget(
            budget_m.group(1) if budget_m else None
        ),
        summary=summary,
        why_it_fits=why,
    )


def _result_from_dict(data: dict) -> AIQualificationResult:
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
    if flex.is_lead is True and score < 50:
        score = 55
    if flex.is_lead is False and score >= 50:
        score = min(score, 40)

    return AIQualificationResult(
        status=status,
        score=score,
        estimated_budget=_normalize_budget(flex.estimated_budget),
        summary=(flex.summary or "").strip() or None,
        why_it_fits=why,
    )


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

    return _result_from_dict(data)


def _build_client(api_key: str):
    from google import genai

    return genai.Client(api_key=api_key)


def _generate_sync(
    client,
    model: str,
    text: str,
    *,
    use_schema: bool = True,
    max_output_tokens: int = 1024,
) -> str:
    from google.genai import types

    config_kwargs: dict = {
        "system_instruction": SYSTEM_PROMPT,
        "temperature": 0.1,
        "max_output_tokens": max_output_tokens,
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


def _models_to_try(primary: str) -> list[str]:
    ordered: list[str] = []
    for name in (primary, *GEMINI_MODEL_FALLBACKS):
        if name and name not in ordered:
            ordered.append(name)
    return ordered


def _trim_lead_text(text: str) -> str:
    cleaned = text.strip()
    if len(cleaned) <= MAX_LEAD_INPUT_CHARS:
        return cleaned
    return cleaned[:MAX_LEAD_INPUT_CHARS] + "\n...[truncated]"


def _is_parse_error(result: AIQualificationResult) -> bool:
    return result.why_it_fits.startswith("Некорректный structured output")


def _error_result(message: str) -> AIQualificationResult:
    return AIQualificationResult(
        status=LeadApprovalStatus.REJECTED,
        score=0,
        estimated_budget=EstimatedBudget.UNKNOWN,
        summary=None,
        why_it_fits=message,
    )


async def _call_and_parse(
    loop: asyncio.AbstractEventLoop,
    client,
    model_name: str,
    text: str,
    *,
    use_schema: bool,
    max_output_tokens: int = 1024,
) -> tuple[AIQualificationResult, str]:
    generate = partial(
        _generate_sync,
        client,
        model_name,
        text,
        use_schema=use_schema,
        max_output_tokens=max_output_tokens,
    )
    raw = await loop.run_in_executor(None, generate)

    if _looks_truncated(raw) and max_output_tokens < 2048:
        logger.warning(
            "Gemini output truncated on %s — retry with 2048 tokens",
            model_name,
        )
        raw = await loop.run_in_executor(
            None,
            partial(
                _generate_sync,
                client,
                model_name,
                text,
                use_schema=use_schema,
                max_output_tokens=2048,
            ),
        )

    try:
        return _parse_response(raw), raw
    except (json.JSONDecodeError, ValidationError) as exc:
        salvaged = _salvage_partial_json(raw)
        if salvaged:
            logger.warning(
                "Salvaged truncated Gemini JSON from %s (schema=%s)",
                model_name,
                use_schema,
            )
            return salvaged, raw
        logger.error(
            "Invalid Gemini JSON (%s schema=%s): %s — raw: %s",
            model_name,
            use_schema,
            exc,
            raw[:500],
        )
        return _error_result("Некорректный structured output от Gemini"), raw


async def qualify_lead(text: str) -> AIQualificationResult:
    settings = get_settings()
    text = _trim_lead_text(text)

    if not settings.gemini_api_key:
        logger.warning("GEMINI_API_KEY not set")
        return _error_result("API-ключ Gemini не настроен")

    client = _build_client(settings.gemini_api_key)
    loop = asyncio.get_running_loop()
    last_exc: Exception | None = None
    result = _error_result("Ошибка Gemini API")
    raw = ""
    parsed_ok = False
    last_parse_fail: Optional[AIQualificationResult] = None

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

            last_parse_fail = result
        except Exception as exc:
            last_exc = exc
            err = str(exc).lower()
            if "404" in err or "not found" in err:
                logger.warning("Gemini model %s unavailable: %s", model_name, exc)
                continue
            logger.exception("Gemini API error: %s", exc)
            return _error_result(f"Ошибка Gemini API: {exc}")

    if not parsed_ok:
        for model_name in _models_to_try(settings.gemini_model):
            try:
                result, raw = await _call_and_parse(
                    loop, client, model_name, text, use_schema=False
                )
                if not _is_parse_error(result):
                    parsed_ok = True
                    logger.info("Gemini plain-JSON fallback OK: %s", model_name)
                    break
                last_parse_fail = result
            except Exception as exc:
                last_exc = exc
                logger.warning("Gemini plain-JSON %s failed: %s", model_name, exc)

    if not parsed_ok:
        salvaged = _salvage_partial_json(raw)
        if salvaged:
            result = salvaged
            parsed_ok = True
        elif last_parse_fail:
            return last_parse_fail
        elif last_exc:
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
