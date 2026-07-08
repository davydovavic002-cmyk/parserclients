from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Final

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parent
_ENV_FILE = _PROJECT_ROOT / ".env"


# ---------------------------------------------------------------------------
# Keyword matrix (case-insensitive matching in filters.py)
# ---------------------------------------------------------------------------

KEYWORDS_EN: Final[list[str]] = [
    "freelance project",
    "contract project",
    "one-off project",
    "need website for brand",
    "fashion brand website",
    "lifestyle brand website",
    "restaurant website",
    "food brand website",
    "music artist website",
    "wellness brand website",
    "health brand website",
    "fitness brand website",
    "sports brand website",
    "education platform website",
    "e-commerce store build",
    "online shop website",
    "crypto project website",
    "web3 landing page",
    "nft project website",
    "creative brand website",
    "boutique brand website",
    "dtc brand website",
    "indie project website",
    "landing page for launch",
    "website redesign project",
    "figma to website",
    "build mvp for startup",
    "need web designer for",
    "looking for developer for project",
    # fullstack — project-based only
    "fullstack developer for project",
    "full stack freelancer needed",
    "hire fullstack for mvp",
    "build web app freelance project",
    "nextjs developer freelance",
    "react developer contract project",
    "supabase mvp build",
    "saas mvp freelance",
    "web application freelance project",
    "need backend and frontend",
    "fullstack contract project",
    "indie hacker need developer",
    "[hiring]",
    "looking to hire",
    "seeking a web designer",
    "seeking a developer",
    "seeking freelance",
    "small business needs website",
    "startup needs website",
    "need someone to build",
    "need help building a website",
    "need a landing page",
    "website for my business",
    "redesign my website",
    "figma to react",
    "figma to nextjs",
    "contract web developer",
    "contract ui designer",
    "remote freelance project",
    "paid freelance gig",
    "project-based contract",
    "fixed budget project",
    "budget for website",
    "hire for a project",
    "freelancer needed for",
    "looking for a contractor",
    "need ui ux designer",
    "need frontend developer",
    "need full stack developer",
    "mvp developer needed",
    "saas prototype needed",
    "build my startup website",
    "client project",
]

KEYWORDS_DE: Final[list[str]] = [
    "Webdesign gesucht",
    "Website erstellen",
    "Homepage erstellen lassen",
    "UI/UX Design Agentur",
    "Landingpage bauen",
    "Relaunch website",
    "Figma Designer gesucht",
    "Freelancer Webentwicklung",
    "Unterstützung Webdesign",
    "Webentwickler für Projekt",
    "Suchen Webagentur",
    "Fullstack Entwickler",
    "mvp erstellen",
    "Fullstack Projekt",
    "Webapp entwickeln lassen",
    "React Entwickler Freelance",
]

KEYWORDS_RU: Final[list[str]] = [
    "ищу дизайнера",
    "ищу разработчика",
    "нужен дизайнер",
    "нужен разработчик",
    "нужен верстальщик",
    "заказ на сайт",
    "заказ на лендинг",
    "разработка сайта",
    "сделать сайт",
    "сделать лендинг",
    "лендинг на заказ",
    "сайт на заказ",
    "нужен mvp",
    "mvp на заказ",
    "ищу фрилансера",
    "удаленный проект",
    "разовый проект",
    "тз на сайт",
    "figma в код",
    "верстка сайта",
    "fullstack на заказ",
    "нужен fullstack",
    "интернет-магазин на заказ",
    "редизайн сайта",
]

KEYWORDS_AM: Final[list[str]] = [
    "veb dizayn",
    "վեբ դիզայն",
    "kayqi patver",
    "կայքերի պատվեր",
    "կայքի պատրաստում",
    "veb tsragravorogh",
    "վեբ ծրագրավորող",
    "landing page patvirel",
]

KEYWORDS_KR: Final[list[str]] = [
    "웹 디자인",
    "홈페이지 제작",
    "외주 개발",
    "MVP 제작",
    "웹사이트 리뉴얼",
    "웹디자이너 구인",
    "반응형 웹 제작",
    "피그마 디자인",
]

KEYWORDS_FR: Final[list[str]] = [
    "création site web",
    "développeur fullstack freelance",
    "design de site",
    "refonte site internet",
    "recherche developpeur web",
    "conception site e-commerce",
    "maquette figma freelance",
]

KEYWORDS_XHS: Final[list[str]] = [
    "全栈开发",
    "独立开发者",
    "高端网页设计",
    "MVP开发",
    "网页设计",
    "网站开发",
    "UI设计",
    "独立站制作",
    "跨境电商建站",
    "前端开发",
    "小程序开发",
    "web design",
    "ui ux design",
    "website creation",
    "y2k UI layout",
]

ALL_KEYWORDS: Final[list[str]] = (
    KEYWORDS_EN
    + KEYWORDS_DE
    + KEYWORDS_AM
    + KEYWORDS_KR
    + KEYWORDS_FR
    + KEYWORDS_XHS
)

BOARDS_KEYWORDS: Final[list[str]] = (
    KEYWORDS_EN + KEYWORDS_DE + KEYWORDS_AM + KEYWORDS_FR + KEYWORDS_KR
)

# ---------------------------------------------------------------------------
# Stop words — freelancer self-promo, portfolio ads, job seekers
# ---------------------------------------------------------------------------

GLOBAL_STOP_WORDS: Final[list[str]] = [
    "for hire",
    "portfolio",
    "i can build",
    "hiring me",
    "open for work",
    "available for freelance",
    "dm me for design",
    "my portfolio",
    "check my work",
    "hire me",
    "specialized in website creation",
    "biete webdesign",
    "ich erstelle",
    "meine referenzen",
    "sucht arbeit",
    "verfügbar als entwickler",
    "포트폴리오",
    "구직중",
    "simple task",
    "quick fix",
]

# CMS / no-code builders — not custom dev (reject unless custom stack also mentioned)
CMS_PLATFORM_MARKERS: Final[list[str]] = [
    "wordpress",
    "word press",
    "wp theme",
    "wp plugin",
    "woocommerce theme",
    "tilda",
    "тильда",
    "webflow",
    "wix",
    "squarespace",
    "bitrix",
    "битрикс",
    "1c-bitrix",
    "1с-битрикс",
    "elementor",
    "divi theme",
    "joomla",
    "opencart",
    "modx",
    "readymag",
    "cargo site",
    "taplink",
]

# Custom code stack — if present alongside CMS mention, may still be a dev project
CUSTOM_DEV_MARKERS: Final[list[str]] = [
    "next.js",
    "nextjs",
    "react",
    "vue",
    "angular",
    "node.js",
    "nodejs",
    "typescript",
    "fullstack",
    "full stack",
    "full-stack",
    "custom development",
    "custom code",
    "figma to code",
    "figma to react",
    "mvp",
    "saas",
    "web app",
    "api integration",
    "supabase",
    "postgresql",
    "backend",
    "frontend developer",
    "from scratch",
    "с нуля",
    "кастомная разработка",
]

# Corporate full-time employment — not project-based leads
CORPORATE_JOB_MARKERS: Final[list[str]] = [
    "full-time",
    "full time",
    "fulltime",
    "permanent position",
    "permanent role",
    "permanent employment",
    "join our team",
    "we're hiring",
    "we are hiring",
    "hiring a senior",
    "looking for a senior",
    "competitive salary",
    "salary range",
    "benefits package",
    "health insurance",
    "401(k)",
    "pension plan",
    "paid time off",
    "on-site only",
    "on site only",
    "in-office",
    "hybrid role",
    "office-based",
    "years of experience",
    "5+ years",
    "3+ years experience",
    "senior software engineer",
    "staff engineer",
    "employment type",
    "w-2",
    "visa sponsorship",
    "fortune 500",
    "global enterprise",
    "in-house role",
    "career opportunity",
    "job opening",
    "job vacancy",
    "annual salary",
    "vollzeit",
    "festanstellung",
    "unbefristet",
    # RU — штатные вакансии, не проекты
    "вакансия",
    "вакансии",
    "ищем в штат",
    "в штат",
    "полная занятость",
    "трудовой договор",
    "офис в москве",
    "офис в санкт",
    "опыт работы от",
    "конкурентная зарплата",
    "з/п от",
    "зарплата от",
    "отправляйте резюме",
    "присылайте резюме",
    "корпоративная культура",
]

# Client/project intent — freelance orders, not job listings
PROJECT_INTENT_MARKERS: Final[list[str]] = [
    "freelance project",
    "contract project",
    "one-off project",
    "fixed price project",
    "hourly contract",
    "need a website",
    "need website for",
    "looking for a freelancer",
    "looking for freelancer",
    "client needs",
    "project budget",
    "build a website",
    "build an mvp",
    "need web designer",
    "need developer for",
    "need designer for",
    "hiring freelancer",
    "[hiring]",
    "paid project",
    "project-based",
    "ищу дизайнера",
    "ищу разработчика",
    "нужен дизайнер",
    "нужен разработчик",
    "заказ на",
    "на заказ",
    "тз на",
    "сделать сайт",
    "сделать лендинг",
    "разработка сайта",
    "удаленный проект",
    "разовый проект",
    "ищу фрилансера",
    "опубликовать заказ",
    "откликнуться на проект",
    # EN — common freelance client posts
    "looking to hire",
    "seeking a freelancer",
    "seeking freelance",
    "small business needs",
    "startup needs",
    "need someone to build",
    "need help building",
    "website for my business",
    "redesign my website",
    "paid gig",
    "paid freelance",
    "contractor needed",
    "freelancer needed",
    "budget is",
    "fixed budget",
    "project deliverables",
    "scope of work",
    "send portfolio",
    "dm for details",
]

# Backward-compatible alias
STOP_WORDS: Final[list[str]] = GLOBAL_STOP_WORDS

# ---------------------------------------------------------------------------
# Telegram global discovery + seed channels
# ---------------------------------------------------------------------------

TG_DISCOVERY_KEYWORDS: Final[list[str]] = [
    # EN — primary discovery
    "freelance website project",
    "need web designer",
    "looking for web developer",
    "brand website project",
    "fullstack mvp freelance",
    "web app freelance",
    "figma to code project",
    "startup mvp developer",
    "client looking for developer",
    "freelance orders",
    "freelance project design",
    "landing page freelance",
    "nextjs freelance project",
    "contract web developer",
    "remote freelance project",
    "small business website project",
    "hire freelance designer",
    "paid freelance website",
    "it freelance remote",
    # RU — secondary
    "заказ на сайт",
    "ищу разработчика",
    "ищу дизайнера",
]

# Backward-compatible alias
TG_DISCOVERY_QUERIES: Final[list[str]] = TG_DISCOVERY_KEYWORDS

# Project/order channels (design + dev). INSERT OR IGNORE on every startup.
STARTING_TELEGRAM_CHANNELS: Final[list[str]] = [
    # EN — client hiring / freelance projects (priority)
    "itfreelancers",
    "Freelanceroff",
    "forhire",
    "freelancehiring",
    "freelancejobupdates",
    "front_end_jobs",
    "remotegeek",
    "webdevl",
    "designerslounge",
    "freelance_jobs_board",
    "remotiveio",
    "remoteok_jobs",
    "freelance_global",
    "webdesigner_jobs",
    "uxui_jobs",
    "devitjobs",
    "freelance_projects_hub",
    # RU — заказы и проекты
    "freeprofi_public",
    "LeadLancer",
    "freelance_orders",
    "vakansii_dlya_dizaynera",
    "job_webdesign",
    "freelancer_group_design",
    "job_developer",
    "jobs_designer",
    # Legacy seeds (keep for existing DB rows)
    "web_dev_jobs",
    "design_jobs",
    "projects_freelance",
]

# ---------------------------------------------------------------------------
# Reddit
# ---------------------------------------------------------------------------

DEFAULT_REDDIT_SUBREDDITS: Final[list[str]] = [
    "forhire",
    "freelance_jobs",
    "creativesforhire",
    "hiring",
    "startups",
    "SideProject",
    "SmallBusiness",
    "Entrepreneur",
    "IndieHackers",
    "SaaS",
    "microsaas",
    "buildinpublic",
    "ecommerce",
    "shopify",
    "webdesign",
    "webdev",
    "web_design",
    "UI_Design",
    "Frontend",
    "reactjs",
    "nextjs",
    "NFT",
    "CryptoCurrency",
    "EuropeFreelance",
    "freelance",
    "EntrepreneurRideAlong",
]

# ---------------------------------------------------------------------------
# Xiaohongshu (XHS) — covered via Google site: + keyword matrix
# ---------------------------------------------------------------------------

XHS_TRENDING_HASHTAGS: Final[list[str]] = [
    "#网页设计",
    "#UI设计",
    "#网站制作",
    "#独立站开发",
    "#程序员独立开发",
    "#创业MVP",
]

# ---------------------------------------------------------------------------
# Open freelance boards (public URLs, no auth)
# ---------------------------------------------------------------------------

BOARDS_URLS: Final[dict[str, str]] = {
    "upwork_design": "https://www.upwork.com/nx/search/jobs/?q=brand+website+design&sort=recency",
    "upwork_fullstack": "https://www.upwork.com/nx/search/jobs/?q=fullstack+mvp+freelance&sort=recency",
    "upwork_landing": "https://www.upwork.com/nx/search/jobs/?q=landing+page+design+freelance&sort=recency",
    "upwork_nextjs": "https://www.upwork.com/nx/search/jobs/?q=nextjs+react+freelance&sort=recency",
    "upwork_figma": "https://www.upwork.com/nx/search/jobs/?q=figma+to+website+freelance&sort=recency",
    "upwork_react": "https://www.upwork.com/nx/search/jobs/?q=react+nextjs+custom+development&sort=recency",
    "fiverr_briefs": "https://www.fiverr.com/categories/graphics-design/website-design",
    "freelancer_design": "https://www.freelancer.com/jobs/website-design/",
    "freelancer_fullstack": "https://www.freelancer.com/jobs/full-stack-development/",
    "freelancer_mvp": "https://www.freelancer.com/jobs/next.js/",
    "guru_com": "https://www.guru.com/d/jobs/c/web-software-development/",
    "peopleperhour": "https://www.peopleperhour.com/freelance-web-development-jobs",
    "peopleperhour_design": "https://www.peopleperhour.com/freelance-web-design-jobs",
    "freelance_de": "https://www.freelance.de/Projekt-auswahl.php",
    "freelancermap": "https://www.freelancermap.com/projektbörse.html",
    "twago_de": "https://www.twago.de/projects/",
}

# ---------------------------------------------------------------------------
# Behance Jobs
# ---------------------------------------------------------------------------

BEHANCE_JOBLIST_URL: Final[str] = "https://www.behance.net/joblist"

BEHANCE_JOB_KEYWORDS: Final[list[str]] = [
    "freelance",
    "contract",
    "project",
    "remote project",
    "brand",
    "fashion",
    "lifestyle",
    "creative",
    "ui/ux",
    "web design",
    "figma",
    "landing page",
]

# ---------------------------------------------------------------------------
# Google Radar — site operators combined with keywords
# ---------------------------------------------------------------------------

GOOGLE_TARGET_SITES: Final[list[str]] = [
    "reddit.com",
    "indiehackers.com",
    "dev.to",
    "producthunt.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "news.ycombinator.com",
    "xiaohongshu.com",
]

GOOGLE_RADAR_KEYWORDS: Final[list[str]] = [
    "need website for fashion brand",
    "lifestyle brand website project",
    "crypto project landing page",
    "e-commerce store build project",
    "freelance website project",
    "fullstack developer freelance project",
    "build mvp freelance contract",
    "nextjs react freelance project",
    "web app mvp for startup",
    "saas mvp developer needed",
    "looking for freelance web designer",
    "client needs website built",
    "looking to hire web developer",
    "seeking freelance ui designer",
    "small business needs website",
    "startup needs landing page",
    "figma to react freelance",
    "contract web developer needed",
    "remote freelance web project",
    "paid freelance website project",
    "hire freelancer for mvp",
    "Webdesign gesucht Projekt",
]

# Skip job-board pages before fetch (saves Gemini calls)
GOOGLE_BLOCKED_URL_PARTS: Final[list[str]] = [
    "/jobs/",
    "/job/",
    "/vacancy",
    "/vacancies",
    "/careers/",
    "/career/",
    "linkedin.com/jobs",
    "linkedin.com/company",
    "indeed.com",
    "glassdoor.com",
    "hh.ru",
    "superjob.ru",
    "habr.com/vacancies",
    "angel.co/company",
    "dribbble.com/jobs",
    "behance.net/joblist",
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @field_validator(
        "notification_tg_bot_token",
        "notification_tg_chat_id",
        "gemini_api_key",
        mode="before",
    )
    @classmethod
    def _strip_env_value(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().strip('"').strip("'")
        return value

    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-2.5-flash", alias="GEMINI_MODEL")

    telegram_api_id: int = Field(
        default=0,
        validation_alias=AliasChoices("TELEGRAM_API_ID", "TG_API_ID"),
    )
    telegram_api_hash: str = Field(
        default="",
        validation_alias=AliasChoices("TELEGRAM_API_HASH", "TG_API_HASH"),
    )
    telegram_session: str = Field(
        default="lead_parser_session", alias="TELEGRAM_SESSION"
    )

    reddit_client_id: str = Field(default="", alias="REDDIT_CLIENT_ID")
    reddit_client_secret: str = Field(default="", alias="REDDIT_CLIENT_SECRET")
    reddit_user_agent: str = Field(
        default="WebDevScoutBot/1.0 by /u/yourusername",
        alias="REDDIT_USER_AGENT",
    )
    reddit_subreddits: list[str] = Field(
        default_factory=lambda: list(DEFAULT_REDDIT_SUBREDDITS)
    )

    db_path: str = Field(default="leads.db", alias="DB_PATH")
    poll_interval_seconds: int = Field(default=240, alias="POLL_INTERVAL_SECONDS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    enable_ai_classifier: bool = Field(default=True)

    # Telegram rate-limit guards
    tg_search_delay_min: float = Field(default=30.0, alias="TG_SEARCH_DELAY_MIN")
    tg_search_delay_max: float = Field(default=60.0, alias="TG_SEARCH_DELAY_MAX")
    tg_join_delay_min: float = Field(default=90.0, alias="TG_JOIN_DELAY_MIN")
    tg_join_delay_max: float = Field(default=240.0, alias="TG_JOIN_DELAY_MAX")
    tg_poll_delay_min: float = Field(default=2.0, alias="TG_POLL_DELAY_MIN")
    tg_poll_delay_max: float = Field(default=5.0, alias="TG_POLL_DELAY_MAX")
    tg_join_daily_min: int = Field(default=3, alias="TG_JOIN_DAILY_MIN")
    tg_join_daily_max: int = Field(default=15, alias="TG_JOIN_DAILY_MAX")
    tg_discovery_interval_seconds: int = Field(
        default=21600, alias="TG_DISCOVERY_INTERVAL_SECONDS"
    )

    # Google Radar
    google_radar_enabled: bool = Field(default=True, alias="GOOGLE_RADAR_ENABLED")
    google_search_delay: float = Field(default=10.0, alias="GOOGLE_SEARCH_DELAY")
    google_results_per_query: int = Field(default=8, alias="GOOGLE_RESULTS_PER_QUERY")
    google_recency_hours: int = Field(default=48, alias="GOOGLE_RECENCY_HOURS")
    google_fetch_timeout: float = Field(default=15.0, alias="GOOGLE_FETCH_TIMEOUT")
    google_max_queries_per_poll: int = Field(
        default=18, alias="GOOGLE_MAX_QUERIES_PER_POLL"
    )

    # Xiaohongshu (Playwright)
    xhs_enabled: bool = Field(default=True, alias="XHS_ENABLED")
    xhs_page_delay: float = Field(default=5.0, alias="XHS_PAGE_DELAY")
    xhs_poll_delay: float = Field(default=8.0, alias="XHS_POLL_DELAY")
    xhs_headless: bool = Field(default=True, alias="XHS_HEADLESS")
    xhs_storage_state: str = Field(default="", alias="XHS_STORAGE_STATE")

    # Freelance boards (Playwright)
    boards_enabled: bool = Field(default=True, alias="BOARDS_ENABLED")
    boards_delay_min: float = Field(default=5.0, alias="BOARDS_DELAY_MIN")
    boards_delay_max: float = Field(default=15.0, alias="BOARDS_DELAY_MAX")
    boards_headless: bool = Field(default=True, alias="BOARDS_HEADLESS")

    # Naver (Playwright)
    naver_enabled: bool = Field(default=True, alias="NAVER_ENABLED")
    naver_delay_min: float = Field(default=5.0, alias="NAVER_DELAY_MIN")
    naver_delay_max: float = Field(default=15.0, alias="NAVER_DELAY_MAX")
    naver_recency_hours: int = Field(default=24, alias="NAVER_RECENCY_HOURS")

    # Behance Jobs (Playwright)
    behance_enabled: bool = Field(default=False, alias="BEHANCE_ENABLED")
    behance_joblist_url: str = Field(default=BEHANCE_JOBLIST_URL, alias="BEHANCE_JOBLIST_URL")
    behance_delay_min: float = Field(default=5.0, alias="BEHANCE_DELAY_MIN")
    behance_delay_max: float = Field(default=15.0, alias="BEHANCE_DELAY_MAX")
    behance_headless: bool = Field(default=True, alias="BEHANCE_HEADLESS")

    # Lead quality gates (lower score = more leads)
    min_lead_score: int = Field(default=50, alias="MIN_LEAD_SCORE")
    max_proposals: int = Field(default=40, alias="MAX_PROPOSALS")
    max_post_age_hours: int = Field(default=72, alias="MAX_POST_AGE_HOURS")
    reject_low_budget: bool = Field(default=False, alias="REJECT_LOW_BUDGET")

    # Lead notifications (separate Telegram bot → personal chat)
    notification_tg_bot_token: str = Field(
        default="", alias="NOTIFICATION_TG_BOT_TOKEN"
    )
    notification_tg_chat_id: str = Field(
        default="", alias="NOTIFICATION_TG_CHAT_ID"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
