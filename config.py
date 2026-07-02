from __future__ import annotations

from functools import lru_cache
from typing import Final

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------------------------------------------------------------------------
# Keyword matrix (case-insensitive matching in filters.py)
# ---------------------------------------------------------------------------

KEYWORDS_EN: Final[list[str]] = [
    "need web design",
    "looking for ui/ux",
    "landing page design",
    "website redesign",
    "figma designer needed",
    "saas design",
    "ux ui consultant",
    "branding and website",
    "need mvp",
    "build a website",
    "web application development",
    "looking for development agency",
    "outsource web development",
    "nextjs developer needed",
    "react supabase web",
    "fullstack dev needed",
    "fix website layout",
    "site is down",
    "wordpress migration",
    "web app broken",
    "hire frontend developer",
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
]

KEYWORDS_RU: Final[list[str]] = [
    "нужен веб дизайн",
    "дизайн лендинга",
    "редизайн сайта",
    "отрисовать в фигме",
    "ui/ux дизайн на заказ",
    "дизайн веб-интерфейсов",
    "нужен сайт под ключ",
    "разработка веб приложения",
    "собрать mvp",
    "разработчик на проект",
    "ищем подрядчика на веб",
    "прикрутить бэкенд",
    "нужен фронтендер",
    "разработка на next.js",
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
    + KEYWORDS_RU
    + KEYWORDS_AM
    + KEYWORDS_KR
    + KEYWORDS_FR
    + KEYWORDS_XHS
)

# 6-language matrix for freelance boards (EN, DE, RU, AM, FR, KR)
BOARDS_KEYWORDS: Final[list[str]] = (
    KEYWORDS_EN + KEYWORDS_DE + KEYWORDS_RU + KEYWORDS_AM + KEYWORDS_FR + KEYWORDS_KR
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
    "ищу заказы",
    "портфолио",
    "сделаю сайт",
    "готов к работе",
    "принимаю заказы",
    "разработаю дизайн для вас",
    "пишите в лс примеры",
    "опыт работы более",
    "포트폴리오",
    "구직중",
]

# Backward-compatible alias
STOP_WORDS: Final[list[str]] = GLOBAL_STOP_WORDS

# ---------------------------------------------------------------------------
# Telegram global discovery + seed channels
# ---------------------------------------------------------------------------

TG_DISCOVERY_KEYWORDS: Final[list[str]] = [
    "фриланс",
    "удаленка",
    "бизнес армения",
    "web dev jobs",
    "startup projects",
    "freelance germany",
    "ереван ит",
    "yerevan digital",
    "it relocants",
    "digital outsourcing",
]

# Backward-compatible alias
TG_DISCOVERY_QUERIES: Final[list[str]] = TG_DISCOVERY_KEYWORDS

STARTING_TELEGRAM_CHANNELS: Final[list[str]] = [
    "armenia_business_chat",
    "yerevan_jobs",
    "armenia_freelance",
    "relocants_armenia",
    "freelance_orders",
    "web_dev_jobs",
    "design_jobs",
    "projects_freelance",
    "finder_vc",
]

# ---------------------------------------------------------------------------
# Reddit
# ---------------------------------------------------------------------------

DEFAULT_REDDIT_SUBREDDITS: Final[list[str]] = [
    "forhire",
    "freelance_jobs",
    "webdev",
    "designjobs",
    "DesignJobs",
    "creativesforhire",
    "startups",
    "SideProject",
    "SmallBusiness",
    "Entrepreneur",
    "IndieHackers",
    "saas",
    "Business_Ideas",
    "growthhacking",
    "de_EDV",
    "BerlinStartupJobs",
    "EuropeFreelance",
]

# ---------------------------------------------------------------------------
# VK communities
# ---------------------------------------------------------------------------

VK_TARGET_COMMUNITIES: Final[list[str]] = [
    "freelance_orders",
    "web_dev_jobs",
    "cerebro_vk",
    "targethunter",
    "startup_ideas",
    "подслушано_инфобизнес",
    "digital_jobs",
    "design_fl",
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
    "upwork_search": "https://www.upwork.com/nx/search/jobs/?q=web+development&sort=recency",
    "fiverr_briefs": "https://www.fiverr.com/gigs/web-development",
    "freelancer_com": "https://www.freelancer.com/jobs/web-development/",
    "guru_com": "https://www.guru.com/d/jobs/c/web-software-development/",
    "peopleperhour": "https://www.peopleperhour.com/freelance-web-development-jobs",
    "freelance_de": "https://www.freelance.de/Projekt-auswahl.php",
    "freelancermap": "https://www.freelancermap.com/projektbörse.html",
    "twago_de": "https://www.twago.de/projects/",
}

# ---------------------------------------------------------------------------
# Habr Freelance + Behance Jobs
# ---------------------------------------------------------------------------

# Habr Career (Freelance closed 2025-02-28; RSS returns HTTP 410)
HABR_CAREER_VACANCIES_URL: Final[str] = "https://career.habr.com/vacancies?type=all"
HABR_FREELANCE_RSS_URL: Final[str] = "https://freelance.habr.com/tasks.rss"

BEHANCE_JOBLIST_URL: Final[str] = "https://www.behance.net/joblist"

BEHANCE_JOB_KEYWORDS: Final[list[str]] = [
    "ui/ux",
    "ui ux",
    "web design",
    "figma",
    "frontend",
    "front-end",
    "website",
    "product design",
    "visual design",
    "ux designer",
    "web developer",
    "landing page",
    "wordpress",
    "react",
    "design system",
]

HABR_KEYWORDS: Final[list[str]] = KEYWORDS_RU + KEYWORDS_EN

# ---------------------------------------------------------------------------
# Google Radar — site operators combined with keywords
# ---------------------------------------------------------------------------

GOOGLE_TARGET_SITES: Final[list[str]] = [
    "instagram.com",
    "indiehackers.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "linkedin.com/posts",
    "dev.to",
    "producthunt.com",
    "xiaohongshu.com",
]

GOOGLE_RADAR_KEYWORDS: Final[list[str]] = [
    "need web design",
    "looking for a developer",
    "ищу веб-дизайнера",
    "need MVP",
    "build a website",
    "Webdesign gesucht",
    "нужен сайт под ключ",
    "hire frontend developer",
    "looking for ui/ux",
    "网页设计",
    "独立站制作",
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-1.5-flash", alias="GEMINI_MODEL")

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

    vk_api_token: str = Field(default="", alias="VK_API_TOKEN")

    db_path: str = Field(default="leads.db", alias="DB_PATH")
    poll_interval_seconds: int = Field(default=300, alias="POLL_INTERVAL_SECONDS")
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
    tg_join_daily_max: int = Field(default=5, alias="TG_JOIN_DAILY_MAX")
    tg_discovery_interval_seconds: int = Field(
        default=21600, alias="TG_DISCOVERY_INTERVAL_SECONDS"
    )

    # Google Radar
    google_radar_enabled: bool = Field(default=True, alias="GOOGLE_RADAR_ENABLED")
    google_search_delay: float = Field(default=10.0, alias="GOOGLE_SEARCH_DELAY")
    google_results_per_query: int = Field(default=8, alias="GOOGLE_RESULTS_PER_QUERY")
    google_recency_hours: int = Field(default=48, alias="GOOGLE_RECENCY_HOURS")
    google_fetch_timeout: float = Field(default=15.0, alias="GOOGLE_FETCH_TIMEOUT")

    # VK API
    vk_api_version: str = Field(default="5.199", alias="VK_API_VERSION")
    vk_poll_delay: float = Field(default=1.5, alias="VK_POLL_DELAY")
    vk_posts_per_community: int = Field(default=25, alias="VK_POSTS_PER_COMMUNITY")

    # Xiaohongshu (Playwright)
    xhs_enabled: bool = Field(default=True, alias="XHS_ENABLED")
    xhs_page_delay: float = Field(default=3.0, alias="XHS_PAGE_DELAY")
    xhs_poll_delay: float = Field(default=5.0, alias="XHS_POLL_DELAY")

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

    # Habr Career (Freelance RSS closed — Career is primary)
    habr_enabled: bool = Field(default=True, alias="HABR_ENABLED")
    habr_career_url: str = Field(default=HABR_CAREER_VACANCIES_URL, alias="HABR_CAREER_URL")
    habr_rss_url: str = Field(default=HABR_FREELANCE_RSS_URL, alias="HABR_RSS_URL")
    habr_try_rss: bool = Field(default=False, alias="HABR_TRY_RSS")
    habr_fetch_timeout: float = Field(default=20.0, alias="HABR_FETCH_TIMEOUT")
    habr_max_items: int = Field(default=40, alias="HABR_MAX_ITEMS")

    # Behance Jobs (Playwright)
    behance_enabled: bool = Field(default=True, alias="BEHANCE_ENABLED")
    behance_joblist_url: str = Field(default=BEHANCE_JOBLIST_URL, alias="BEHANCE_JOBLIST_URL")
    behance_delay_min: float = Field(default=5.0, alias="BEHANCE_DELAY_MIN")
    behance_delay_max: float = Field(default=15.0, alias="BEHANCE_DELAY_MAX")
    behance_headless: bool = Field(default=True, alias="BEHANCE_HEADLESS")

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
