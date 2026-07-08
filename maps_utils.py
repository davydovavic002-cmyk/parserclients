from __future__ import annotations

import re
from urllib.parse import urlparse

# Only social / aggregator links — not a real brand website
SOCIAL_OR_AGGREGATOR_DOMAINS: frozenset[str] = frozenset(
    {
        "facebook.com",
        "fb.com",
        "instagram.com",
        "linktr.ee",
        "tiktok.com",
        "yelp.com",
        "google.com",
        "g.page",
        "goo.gl",
        "maps.app.goo.gl",
        "doordash.com",
        "ubereats.com",
        "grubhub.com",
        "opentable.com",
        "toasttab.com",
        "chownow.com",
        "clover.com",
        "square.site",
        "wixsite.com",
        "wordpress.com",
        "blogspot.com",
        "canva.site",
    }
)

CHAIN_NAME_MARKERS: tuple[str, ...] = (
    "starbucks",
    "mcdonald",
    "subway",
    "walmart",
    "target",
    "costco",
    "dunkin",
    "chipotle",
    "panera",
    "domino",
    "pizza hut",
    "taco bell",
    "7-eleven",
    "cvs pharmacy",
    "walgreens",
    "bank of america",
    "wells fargo",
    "chase bank",
)


def normalize_place_id(place_id: str) -> str:
    return place_id.removeprefix("places/").strip()


def domain_from_url(url: str) -> str:
    try:
        host = urlparse(url.strip()).netloc.lower()
        return host.removeprefix("www.")
    except Exception:
        return ""


def has_real_website(website: str | None) -> bool:
    if not website or not website.strip():
        return False
    domain = domain_from_url(website)
    if not domain:
        return False
    for blocked in SOCIAL_OR_AGGREGATOR_DOMAINS:
        if domain == blocked or domain.endswith("." + blocked):
            return False
    return True


def is_chain_business(name: str) -> bool:
    lowered = name.lower()
    return any(marker in lowered for marker in CHAIN_NAME_MARKERS)


def build_maps_lead_text(
    *,
    name: str,
    category: str,
    city: str,
    address: str,
    phone: str,
    types: list[str],
    maps_url: str,
) -> str:
    type_str = ", ".join(types[:6]) if types else category
    return (
        f"US local business prospect (Google Maps — no website)\n"
        f"Business: {name}\n"
        f"Category search: {category}\n"
        f"City: {city}\n"
        f"Address: {address}\n"
        f"Phone: {phone or '(none)'}\n"
        f"Types: {type_str}\n"
        f"Website: none — potential custom brand website client\n"
        f"Google Maps: {maps_url}\n"
        f"Market: USA outbound — independent business without a real website."
    )
