from __future__ import annotations

import logging
import unicodedata
from typing import Final, Optional

from config import (
    BEHANCE_JOB_KEYWORDS,
    BOARDS_KEYWORDS,
    GLOBAL_STOP_WORDS,
    KEYWORDS_DE,
    KEYWORDS_EN,
    KEYWORDS_KR,
    KEYWORDS_RU,
    KEYWORDS_XHS,
)
from models import LeadSource

logger = logging.getLogger(__name__)

TG_KEYWORDS = KEYWORDS_RU + KEYWORDS_EN + KEYWORDS_DE
REDDIT_KEYWORDS = KEYWORDS_EN + KEYWORDS_DE
VK_KEYWORDS = KEYWORDS_RU
NAVER_KEYWORDS = KEYWORDS_KR

# Short tokens — job titles / board cards rarely contain full phrases like
# "need web design"; without these, pre-filter blocks everything before Gemini.
CORE_WEB_TOKENS: Final[list[str]] = [
    "frontend",
    "front-end",
    "figma",
    "ui/ux",
    "ui ux",
    "web design",
    "website",
    "landing",
    "wordpress",
    "react",
    "next.js",
    "nextjs",
    "vue",
    "mvp",
    "веб",
    "дизайн",
    "верст",
    "лендинг",
    "разработ",
    "figma",
    "tailwind",
    "tilda",
    "bitrix",
]


def _normalize(text: str) -> str:
    """
    Unicode-safe normalization for CJK (中文/한글) and Latin scripts.
    NFKC folds full-width chars; casefold handles Latin case-insensitivity
    without breaking Hangul or Han characters.
    """
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = normalized.casefold()
    return " ".join(normalized.split())


def _keyword_in_text(keyword: str, text: str) -> bool:
    kw = unicodedata.normalize("NFKC", keyword).casefold().strip()
    if not kw:
        return False
    haystack = _normalize(text)
    return kw in haystack


def _matches(text: str, keywords: list[str]) -> bool:
    return any(_keyword_in_text(kw, text) for kw in keywords)


def has_stop_words(text: str) -> bool:
    return any(_keyword_in_text(sw, text) for sw in GLOBAL_STOP_WORDS)


def _base_check(text: str, keywords: list[str], *, allow_core: bool = False) -> bool:
    if not text or not text.strip():
        return False
    if has_stop_words(text):
        return False
    matched = _matches(text, keywords) or (
        allow_core and _matches(text, CORE_WEB_TOKENS)
    )
    if not matched:
        logger.debug(
            "Pre-filter: no keyword match (text len=%d, sample=%r)",
            len(text),
            text[:80],
        )
    return matched


def passes_tg_filter(text: str) -> bool:
    return _base_check(text, TG_KEYWORDS)


def passes_reddit_filter(text: str) -> bool:
    return _base_check(text, REDDIT_KEYWORDS)


def passes_vk_filter(text: str) -> bool:
    return _base_check(text, VK_KEYWORDS)


def passes_xhs_filter(text: str) -> bool:
    if not text or not text.strip():
        return False
    if has_stop_words(text):
        return False
    return _matches(text, KEYWORDS_XHS)


def passes_boards_filter(text: str) -> bool:
    return _base_check(text, BOARDS_KEYWORDS, allow_core=True)


def passes_naver_filter(text: str) -> bool:
    return _base_check(text, NAVER_KEYWORDS)


def passes_habr_filter(text: str) -> bool:
    """Habr Career search is already targeted — only drop freelancer spam."""
    if not text or not text.strip():
        return False
    if has_stop_words(text):
        return False
    return True


def passes_behance_filter(text: str) -> bool:
    if not text or not text.strip():
        return False
    if has_stop_words(text):
        return False
    return _matches(text, BEHANCE_JOB_KEYWORDS)


def passes_prefilter(text: str, source: Optional[LeadSource] = None) -> bool:
    filters = {
        LeadSource.TELEGRAM: passes_tg_filter,
        LeadSource.REDDIT: passes_reddit_filter,
        LeadSource.VK: passes_vk_filter,
        LeadSource.XHS: passes_xhs_filter,
        LeadSource.BOARDS: passes_boards_filter,
        LeadSource.NAVER: passes_naver_filter,
        LeadSource.HABR: passes_habr_filter,
        LeadSource.BEHANCE: passes_behance_filter,
        LeadSource.GOOGLE: lambda t: not has_stop_words(t) and bool(t.strip()),
    }
    if source is None:
        return passes_tg_filter(text)
    fn = filters.get(source)
    return fn(text) if fn else passes_tg_filter(text)
