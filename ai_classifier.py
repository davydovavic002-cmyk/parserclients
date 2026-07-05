from __future__ import annotations

import asyncio
import json
import logging

from pydantic import ValidationError

from config import get_settings
from models import (
    AIQualificationResult,
    EstimatedBudget,
    LeadApprovalStatus,
)

logger = logging.getLogger(__name__)

GEMINI_MODEL_FALLBACKS: tuple[str, ...] = (
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-flash-latest",
)

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

## estimated_budget values (use exactly one)
- High — likely $1,500+
- Medium — roughly $1,000-$1,500
- Low — under $1,000 or strong low-budget signals
- Unknown — no budget cues; infer from scope when possible

## OUTPUT FIELDS (JSON)
- status: "Approved" or "Rejected"
- score: integer 0-100
- estimated_budget: "High" | "Medium" | "Low" | "Unknown"
- summary: 1-2 sentence task essence (null if Rejected and no real task)
- why_it_fits: concise explanation in Russian — why approved or why rejected
"""


def _build_client(api_key: str):
    from google import genai

    return genai.Client(api_key=api_key)


def _generate_sync(client, model: str, text: str) -> str:
    from google.genai import types

    response = client.models.generate_content(
        model=model,
        contents=text,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.1,
            max_output_tokens=450,
            response_mime_type="application/json",
            response_schema=AIQualificationResult,
        ),
    )
    return response.text or "{}"


def _models_to_try(primary: str) -> list[str]:
    ordered: list[str] = []
    for name in (primary, *GEMINI_MODEL_FALLBACKS):
        if name and name not in ordered:
            ordered.append(name)
    return ordered


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

    for model_name in _models_to_try(settings.gemini_model):
        try:
            raw = await loop.run_in_executor(
                None,
                _generate_sync,
                client,
                model_name,
                text,
            )
            if model_name != settings.gemini_model:
                logger.info("Gemini fallback model used: %s", model_name)
            break
        except Exception as exc:
            last_exc = exc
            err = str(exc).lower()
            if "404" in err or "not found" in err:
                logger.warning("Gemini model %s unavailable: %s", model_name, exc)
                continue
            logger.exception("Gemini API error: %s", exc)
            return _error_result(f"Ошибка Gemini API: {exc}")
    else:
        logger.error("All Gemini models failed: %s", last_exc)
        return _error_result(f"Ошибка Gemini API: {last_exc}")

    try:
        result = AIQualificationResult.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        logger.error("Invalid Gemini JSON: %s — raw: %s", exc, raw)
        try:
            result = AIQualificationResult.model_validate_json(raw)
        except ValidationError:
            return _error_result("Некорректный structured output от Gemini")

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
