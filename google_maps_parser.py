from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

import httpx

from config import MAPS_BUSINESS_CATEGORIES, MAPS_US_CITIES, get_settings
from filters import passes_maps_filter
from maps_utils import (
    build_maps_lead_text,
    has_real_website,
    is_chain_business,
    normalize_place_id,
)
from models import LeadSource, RawPost

logger = logging.getLogger(__name__)

PostHandler = Callable[[RawPost], Awaitable[None]]

PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
LEGACY_TEXT_SEARCH = "https://maps.googleapis.com/maps/api/place/textsearch/json"
LEGACY_DETAILS = "https://maps.googleapis.com/maps/api/place/details/json"

FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,"
    "places.nationalPhoneNumber,places.websiteUri,places.googleMapsUri,"
    "places.businessStatus,places.types,places.primaryType"
)


@dataclass
class MapsPlace:
    place_id: str
    name: str
    address: str
    phone: str
    website: str
    maps_url: str
    types: list[str]
    category: str
    city: str


class GoogleMapsParser:
    """Find US businesses on Google Maps without a real website."""

    def __init__(self, on_post: PostHandler) -> None:
        self._settings = get_settings()
        self._on_post = on_post
        self._http: Optional[httpx.AsyncClient] = None
        self._seen_ids: set[str] = set()
        self._search_offset: int = 0
        self._status_detail: str = "не запущен"

    @property
    def status_detail(self) -> str:
        if not self.is_active:
            return getattr(self, "_status_detail", "выключен")
        cities = len(MAPS_US_CITIES)
        cats = len(MAPS_BUSINESS_CATEGORIES)
        return (
            f"USA no-website scan, {cities} cities × {cats} niches, "
            f"{self._settings.maps_searches_per_poll}/poll"
        )

    def _search_batch(self) -> list[tuple[str, str]]:
        combos: list[tuple[str, str]] = []
        for city in MAPS_US_CITIES:
            for category in MAPS_BUSINESS_CATEGORIES:
                combos.append((city, category))
        batch = max(1, self._settings.maps_searches_per_poll)
        start = self._search_offset % max(len(combos), 1)
        chunk = combos[start : start + batch]
        if len(chunk) < batch:
            chunk = chunk + combos[: batch - len(chunk)]
        self._search_offset = (start + batch) % max(len(combos), 1)
        return chunk

    async def _search_new_api(self, query: str) -> list[dict]:
        assert self._http is not None
        api_key = self._settings.google_maps_api_key.strip()
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": FIELD_MASK,
        }
        body = {
            "textQuery": query,
            "languageCode": "en",
            "regionCode": "US",
            "maxResultCount": min(self._settings.maps_results_per_search, 20),
        }
        resp = await self._http.post(PLACES_SEARCH_URL, headers=headers, json=body)
        if resp.status_code != 200:
            logger.warning("Maps New API HTTP %s: %s", resp.status_code, resp.text[:200])
            return []
        return resp.json().get("places") or []

    async def _search_legacy(self, query: str) -> list[dict]:
        assert self._http is not None
        api_key = self._settings.google_maps_api_key.strip()
        resp = await self._http.get(
            LEGACY_TEXT_SEARCH,
            params={"query": query, "key": api_key},
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        if data.get("status") not in ("OK", "ZERO_RESULTS"):
            logger.warning("Maps legacy search: %s", data.get("status"))
            return []
        results = data.get("results") or []
        out: list[dict] = []
        for item in results[: self._settings.maps_results_per_search]:
            pid = item.get("place_id")
            if not pid:
                continue
            detail_resp = await self._http.get(
                LEGACY_DETAILS,
                params={
                    "place_id": pid,
                    "fields": (
                        "name,website,formatted_phone_number,formatted_address,"
                        "url,business_status,types"
                    ),
                    "key": api_key,
                },
            )
            await asyncio.sleep(self._settings.maps_request_delay)
            if detail_resp.status_code != 200:
                continue
            detail = detail_resp.json().get("result") or {}
            out.append(
                {
                    "id": f"places/{pid}",
                    "displayName": {"text": detail.get("name", item.get("name", ""))},
                    "formattedAddress": detail.get(
                        "formatted_address", item.get("formatted_address", "")
                    ),
                    "nationalPhoneNumber": detail.get("formatted_phone_number", ""),
                    "websiteUri": detail.get("website", ""),
                    "googleMapsUri": detail.get("url", ""),
                    "businessStatus": detail.get("business_status", "OPERATIONAL"),
                    "types": detail.get("types") or item.get("types") or [],
                }
            )
        return out

    def _parse_place(self, raw: dict, *, category: str, city: str) -> Optional[MapsPlace]:
        status = raw.get("businessStatus") or raw.get("business_status") or ""
        if status and status != "OPERATIONAL":
            return None

        name = (raw.get("displayName") or {}).get("text") or raw.get("name") or ""
        name = name.strip()
        if len(name) < 2 or is_chain_business(name):
            return None

        website = raw.get("websiteUri") or raw.get("website") or ""
        if has_real_website(website):
            return None

        place_id = normalize_place_id(raw.get("id") or raw.get("place_id") or "")
        if not place_id:
            return None

        return MapsPlace(
            place_id=place_id,
            name=name,
            address=raw.get("formattedAddress") or raw.get("formatted_address") or "",
            phone=raw.get("nationalPhoneNumber")
            or raw.get("formatted_phone_number")
            or "",
            website=website,
            maps_url=raw.get("googleMapsUri") or raw.get("url") or "",
            types=list(raw.get("types") or []),
            category=category,
            city=city,
        )

    async def _search_places(self, city: str, category: str) -> list[MapsPlace]:
        query = f"{category} in {city}"
        raw_places = await self._search_new_api(query)
        if not raw_places:
            raw_places = await self._search_legacy(query)

        places: list[MapsPlace] = []
        for raw in raw_places:
            place = self._parse_place(raw, category=category, city=city)
            if place:
                places.append(place)
        return places

    async def _process_place(self, place: MapsPlace) -> None:
        if place.place_id in self._seen_ids:
            return
        self._seen_ids.add(place.place_id)

        text = build_maps_lead_text(
            name=place.name,
            category=place.category,
            city=place.city,
            address=place.address,
            phone=place.phone,
            types=place.types,
            maps_url=place.maps_url or f"https://www.google.com/maps/search/?api=1&query_place_id={place.place_id}",
        )

        if not passes_maps_filter(text):
            return

        logger.info("Maps: prospect '%s' (%s) — pipeline", place.name, place.city)

        post = RawPost(
            external_id=hashlib.sha256(place.place_id.encode()).hexdigest()[:32],
            source=LeadSource.MAPS,
            text=text,
            author=f"maps_{place.city.replace(' ', '_')[:20]}",
            contact=place.maps_url
            or f"https://www.google.com/maps/search/?api=1&query_place_id={place.place_id}",
            timestamp=datetime.now(timezone.utc),
        )
        await self._on_post(post)

    async def poll_recent(self) -> None:
        if not self._http:
            return

        batch = self._search_batch()
        logger.info("Maps: scanning %d city/category search(es)", len(batch))
        found = 0

        for city, category in batch:
            try:
                places = await self._search_places(city, category)
                logger.info(
                    "Maps [%s / %s]: %d without real website",
                    category,
                    city,
                    len(places),
                )
                found += len(places)
                for place in places:
                    try:
                        await self._process_place(place)
                    except Exception as exc:
                        logger.error("Maps process error: %s", exc)
            except Exception as exc:
                logger.exception("Maps search failed [%s / %s]: %s", category, city, exc)
            await asyncio.sleep(self._settings.maps_request_delay)

        logger.info("Maps: poll done — %d prospect(s) found", found)

    async def start(self) -> None:
        self._status_detail = "не запущен"
        if not self._settings.maps_enabled:
            self._status_detail = "MAPS_ENABLED=false"
            logger.info("Maps parser disabled")
            return

        api_key = self._settings.google_maps_api_key.strip()
        if not api_key:
            self._status_detail = "нет GOOGLE_MAPS_API_KEY в .env"
            logger.warning("Maps parser disabled — GOOGLE_MAPS_API_KEY missing")
            return

        self._http = httpx.AsyncClient(timeout=30.0)
        logger.info(
            "Maps parser ready — %d US cities, %d categories",
            len(MAPS_US_CITIES),
            len(MAPS_BUSINESS_CATEGORIES),
        )

    async def stop(self) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None

    @property
    def is_active(self) -> bool:
        return self._http is not None
