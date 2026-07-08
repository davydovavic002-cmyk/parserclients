from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from models import AIQualificationResult, EstimatedBudget, LeadApprovalStatus, RawPost

_BUDGET_REJECT_HINTS = (
    "бюджет",
    "budget",
    "оплат",
    "too low",
    "ниже",
    "низк",
    "micro-task",
    "микро",
)


def is_budget_only_rejection(reason: str) -> bool:
    r = reason.lower()
    if not any(h in r for h in _BUDGET_REJECT_HINTS):
        return False
    job_hints = ("вакансия", "full-time", "full time", "штат", "corporate", "статья")
    return not any(h in r for h in job_hints)


def approve_unknown_budget_if_eligible(
    result: AIQualificationResult,
    *,
    min_score: int,
) -> AIQualificationResult:
    """
    Gemini often rejects with 'budget too low' while budget=Unknown.
    If score is OK and only budget wording blocked it — approve.
    """
    if result.is_lead:
        return result
    if result.estimated_budget != EstimatedBudget.UNKNOWN:
        return result
    if result.score < min_score:
        return result
    if not is_budget_only_rejection(result.reason):
        return result

    return AIQualificationResult(
        status=LeadApprovalStatus.APPROVED,
        score=result.score,
        estimated_budget=EstimatedBudget.UNKNOWN,
        summary=result.summary,
        why_it_fits=f"Unknown budget — scope OK: {result.reason[:120]}",
    )


def passes_ai_quality_gate(
    result: AIQualificationResult,
    *,
    min_score: int,
    reject_low_budget: bool = True,
) -> tuple[bool, str]:
    if not result.is_lead:
        return False, "AI rejected"

    if result.score < min_score:
        return (
            False,
            f"score {result.score} < {min_score}",
        )

    # Unknown budget always OK; optionally reject explicit Low
    if reject_low_budget and result.estimated_budget == EstimatedBudget.LOW:
        return False, "budget Low (<$500)"

    return True, ""

_PROPOSALS_LESS_THAN_RE = re.compile(
    r"(?i)proposals?\s*:\s*less\s+than\s+(\d+)"
)
_PROPOSALS_RANGE_RE = re.compile(
    r"(?i)proposals?\s*:\s*(\d+)\s*(?:to|-)\s*(\d+)"
)
_PROPOSALS_PLUS_RE = re.compile(r"(?i)proposals?\s*:\s*(\d+)\s*\+")
_PROPOSALS_SINGLE_RE = re.compile(r"(?i)proposals?\s*:\s*(\d+)\b")
_PROPOSALS_INLINE_RE = re.compile(r"(?i)(\d+)\+?\s+proposals?")
_BIDS_RE = re.compile(r"(?i)bids?\s*:\s*(\d+)")
_APPLICANTS_RE = re.compile(r"(?i)(\d+)\+?\s+applicants?")

_POSTED_AGO_RE = re.compile(
    r"(?i)posted\s+(\d+)\s+(minute|hour|day|week)s?\s+ago"
)


def parse_proposal_count(text: str) -> Optional[int]:
    """Return the upper-bound proposal/bid count from listing text."""
    if not text:
        return None

    match = _PROPOSALS_LESS_THAN_RE.search(text)
    if match:
        return max(0, int(match.group(1)) - 1)

    match = _PROPOSALS_RANGE_RE.search(text)
    if match:
        return int(match.group(2))

    match = _PROPOSALS_PLUS_RE.search(text)
    if match:
        return int(match.group(1))

    match = _PROPOSALS_SINGLE_RE.search(text)
    if match:
        return int(match.group(1))

    for pattern in (_PROPOSALS_INLINE_RE, _BIDS_RE, _APPLICANTS_RE):
        match = pattern.search(text)
        if match:
            return int(match.group(1))

    return None


def parse_posted_hours_ago(text: str) -> Optional[float]:
    """Parse 'Posted 2 hours ago' style hints into hours."""
    match = _POSTED_AGO_RE.search(text)
    if not match:
        return None

    amount = int(match.group(1))
    unit = match.group(2).lower()
    if unit.startswith("minute"):
        return amount / 60.0
    if unit.startswith("hour"):
        return float(amount)
    if unit.startswith("day"):
        return amount * 24.0
    if unit.startswith("week"):
        return amount * 24.0 * 7
    return None


def is_post_too_old(timestamp: datetime, max_age_hours: int) -> bool:
    if max_age_hours <= 0:
        return False

    post_time = timestamp
    if post_time.tzinfo is None:
        post_time = post_time.replace(tzinfo=timezone.utc)

    age_hours = (datetime.now(timezone.utc) - post_time).total_seconds() / 3600
    return age_hours > max_age_hours


def should_skip_board_listing(
    text: str,
    *,
    max_proposals: int,
    max_post_age_hours: int,
) -> Optional[str]:
    proposals = parse_proposal_count(text)
    if proposals is not None and proposals >= max_proposals:
        return f"proposals {proposals} >= {max_proposals}"

    posted_hours = parse_posted_hours_ago(text)
    if posted_hours is not None and posted_hours > max_post_age_hours:
        return f"posted {posted_hours:.0f}h ago > {max_post_age_hours}h"

    return None


def should_skip_by_age(post: RawPost, max_age_hours: int) -> Optional[str]:
    if is_post_too_old(post.timestamp, max_age_hours):
        return f"post age > {max_age_hours}h"
    return None
