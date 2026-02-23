"""
Stage 1 — Geo Ingestion: Google Places API (New) client with pagination,
retry logic, rate limiting, field-level cost control, and caching.

Migrated from the legacy Places API to the Places API (New):
  - Text Search:   POST https://places.googleapis.com/v1/places:searchText
  - Place Details: GET  https://places.googleapis.com/v1/places/{placeId}
  - API key passed via X-Goog-Api-Key header
  - Field selection via X-Goog-FieldMask header
"""

from __future__ import annotations

import asyncio
import datetime
from typing import Optional

import httpx
from aiolimiter import AsyncLimiter
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import get_settings
from app.logging_config import logger
from app.db.models import Place

settings = get_settings()

# ── Rate limiter: max N requests per second ──────────────────────
_rate_limiter = AsyncLimiter(
    max_rate=settings.max_requests_per_second,
    time_period=1,
)

# ── In-memory place_id cache for dedup within a session ──────────
_place_id_cache: set[str] = set()

# ── Google Places API (New) constants ────────────────────────────
TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
PLACE_DETAILS_URL = "https://places.googleapis.com/v1/places"  # + /{placeId}

# Field masks for cost-efficient requests (Enterprise SKU fields)
TEXT_SEARCH_FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,"
    "places.location,places.rating,places.userRatingCount,"
    "places.nationalPhoneNumber,places.websiteUri,"
    "places.regularOpeningHours,places.addressComponents,"
    "places.types,places.businessStatus,places.priceLevel,"
    "nextPageToken"
)

DETAILS_FIELD_MASK = (
    "id,displayName,formattedAddress,location,rating,userRatingCount,"
    "nationalPhoneNumber,websiteUri,regularOpeningHours,"
    "addressComponents,types,businessStatus,priceLevel"
)

# priceLevel string → integer mapping
_PRICE_LEVEL_MAP = {
    "PRICE_LEVEL_FREE": 0,
    "PRICE_LEVEL_INEXPENSIVE": 1,
    "PRICE_LEVEL_MODERATE": 2,
    "PRICE_LEVEL_EXPENSIVE": 3,
    "PRICE_LEVEL_VERY_EXPENSIVE": 4,
}


def _normalize_place(raw: dict) -> dict:
    """
    Convert a Places API (New) place object into the flat dict format
    expected by the rest of the application (matching our DB model fields).
    """
    display_name = raw.get("displayName", {})
    location = raw.get("location", {})
    opening = raw.get("regularOpeningHours")
    opening_dict = None
    if opening:
        opening_dict = {
            "open_now": opening.get("openNow"),
            "weekday_text": opening.get("weekdayDescriptions", []),
        }

    price_str = raw.get("priceLevel")
    price_int = _PRICE_LEVEL_MAP.get(price_str) if price_str else None

    return {
        "place_id": raw.get("id", ""),
        "name": display_name.get("text", ""),
        "formatted_address": raw.get("formattedAddress"),
        "latitude": location.get("latitude"),
        "longitude": location.get("longitude"),
        "rating": raw.get("rating"),
        "user_ratings_total": raw.get("userRatingCount"),
        "formatted_phone_number": raw.get("nationalPhoneNumber"),
        "website": raw.get("websiteUri"),
        "opening_hours": opening_dict,
        "address_components": raw.get("addressComponents"),
        "types": raw.get("types"),
        "business_status": raw.get("businessStatus"),
        "price_level": price_int,
    }


class GooglePlacesClient:
    """Async Google Places API (New) client with built-in pagination and retry."""

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None

    def _headers(self, field_mask: str) -> dict:
        """Common headers for Places API (New) requests."""
        return {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": settings.google_places_api_key,
            "X-Goog-FieldMask": field_mask,
        }

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30)
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ── Text Search with pagination ──────────────────────────────

    @retry(
        stop=stop_after_attempt(settings.enrichment_max_retries),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.ConnectError)),
        reraise=True,
    )
    async def _text_search_page(
        self,
        query: str,
        location: Optional[str] = None,
        radius: Optional[int] = None,
        page_token: Optional[str] = None,
    ) -> dict:
        async with _rate_limiter:
            client = await self._get_client()

            body: dict = {
                "textQuery": query,
                "pageSize": 20,
            }

            # Build locationBias from "lat,lng" + radius
            if location:
                try:
                    parts = location.split(",")
                    lat, lng = float(parts[0].strip()), float(parts[1].strip())
                    body["locationBias"] = {
                        "circle": {
                            "center": {"latitude": lat, "longitude": lng},
                            "radius": float(radius) if radius else 5000.0,
                        }
                    }
                except (ValueError, IndexError):
                    logger.warning(f"Invalid location format: {location}, skipping locationBias")

            if page_token:
                body["pageToken"] = page_token

            logger.info(f"Text Search (New) request: query={query}, token={'yes' if page_token else 'no'}")

            resp = await client.post(
                TEXT_SEARCH_URL,
                json=body,
                headers=self._headers(TEXT_SEARCH_FIELD_MASK),
            )
            resp.raise_for_status()
            data = resp.json()

            # The new API returns HTTP errors directly (4xx/5xx) instead of
            # in-body status codes, but check for error object just in case
            if "error" in data:
                error_msg = data["error"].get("message", str(data["error"]))
                logger.error(f"Places API (New) error: {error_msg}")
                raise httpx.HTTPStatusError(
                    message=error_msg,
                    request=resp.request,
                    response=resp,
                )
            return data

    async def text_search(
        self,
        query: str,
        location: Optional[str] = None,
        radius: Optional[int] = None,
        max_pages: int = 3,
    ) -> list[dict]:
        """Execute text search with automatic pagination."""
        all_results: list[dict] = []
        page_token: Optional[str] = None

        for page in range(max_pages):
            data = await self._text_search_page(query, location, radius, page_token)
            results = data.get("places", [])
            all_results.extend(results)
            logger.info(f"Page {page + 1}: {len(results)} results (total {len(all_results)})")

            page_token = data.get("nextPageToken")
            if not page_token:
                break

            # Small delay between pages for politeness
            await asyncio.sleep(0.5)

        return all_results

    async def grid_search(
        self,
        query: str,
        center_lat: float,
        center_lng: float,
        radius_km: float,
        max_pages: int = 3,
        on_progress=None,
    ) -> list[dict]:
        """
        Grid-based search: splits a large area into overlapping sub-circles
        to bypass the 60-result limit per search. Deduplicates by place ID.

        on_progress: optional async callback(current, total, unique_so_far)
        """
        import math

        # Determine grid cell radius — aim for sub-circles of ~2km each
        cell_radius_km = min(2.0, radius_km)
        # How many cells across each axis
        cells_per_axis = max(1, int(math.ceil(radius_km / cell_radius_km)))
        # Cap to avoid excessive API calls (max 5x5 = 25 sub-searches)
        cells_per_axis = min(cells_per_axis, 5)
        cell_radius_km = radius_km / cells_per_axis

        # Convert km offsets to lat/lng deltas
        lat_step = (cell_radius_km * 2) / 111.32  # ~111.32 km per degree lat
        lng_step = (cell_radius_km * 2) / (111.32 * math.cos(math.radians(center_lat)))

        # Build grid centers
        grid_centers = []
        half = (cells_per_axis - 1) / 2.0
        for row in range(cells_per_axis):
            for col in range(cells_per_axis):
                lat = center_lat + (row - half) * lat_step
                lng = center_lng + (col - half) * lng_step
                grid_centers.append((lat, lng))

        logger.info(
            f"Grid search: {len(grid_centers)} sub-regions, "
            f"cell_radius={cell_radius_km:.1f}km, area_radius={radius_km:.1f}km"
        )

        seen_ids: set[str] = set()
        all_results: list[dict] = []
        cell_radius_m = int(cell_radius_km * 1000 * 1.3)  # 30% overlap

        for i, (lat, lng) in enumerate(grid_centers):
            location_str = f"{lat},{lng}"
            try:
                results = await self.text_search(
                    query=query,
                    location=location_str,
                    radius=cell_radius_m,
                    max_pages=max_pages,
                )
                new_count = 0
                for place in results:
                    pid = place.get("id", "")
                    if pid and pid not in seen_ids:
                        seen_ids.add(pid)
                        all_results.append(place)
                        new_count += 1
                logger.info(
                    f"Grid cell {i+1}/{len(grid_centers)}: "
                    f"{len(results)} raw, {new_count} new (total unique: {len(all_results)})"
                )
            except Exception as exc:
                logger.error(f"Grid cell {i+1} failed: {exc}")

            # Fire progress callback
            if on_progress:
                try:
                    await on_progress(i + 1, len(grid_centers), len(all_results))
                except Exception:
                    pass

            # Small delay between sub-searches
            await asyncio.sleep(0.3)

        logger.info(f"Grid search complete: {len(all_results)} unique results")
        return all_results

    # ── Place Details ────────────────────────────────────────────

    @retry(
        stop=stop_after_attempt(settings.enrichment_max_retries),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.ConnectError)),
        reraise=True,
    )
    async def get_place_details(self, place_id: str) -> dict:
        """Fetch place details via Places API (New)."""
        # Check in-memory cache
        if place_id in _place_id_cache:
            logger.debug(f"Cache hit for place_id={place_id}")
            return {}

        async with _rate_limiter:
            client = await self._get_client()
            url = f"{PLACE_DETAILS_URL}/{place_id}"

            resp = await client.get(
                url,
                headers=self._headers(DETAILS_FIELD_MASK),
            )
            resp.raise_for_status()
            data = resp.json()

            if "error" in data:
                logger.warning(f"Place details error for {place_id}: {data['error']}")
                return {}

            _place_id_cache.add(place_id)
            return data


# ── Persistence helpers ──────────────────────────────────────────

async def upsert_places(
    db: AsyncSession,
    raw_results: list[dict],
    search_query: str,
    search_location: Optional[str],
    client: GooglePlacesClient,
) -> list[Place]:
    """Normalize API results, fetch details, upsert into DB, return Place objects."""
    places: list[Place] = []

    for raw in raw_results:
        # The raw result is a Places API (New) object; extract place_id
        gp_id = raw.get("id", "")
        if not gp_id:
            continue

        # Check DB-level dedup
        existing = await db.execute(select(Place).where(Place.place_id == gp_id))
        existing_place = existing.scalar_one_or_none()
        if existing_place:
            places.append(existing_place)
            continue

        # Fetch full details via Place Details (New)
        try:
            details_raw = await client.get_place_details(gp_id)
        except Exception as exc:
            logger.error(f"Failed to fetch details for {gp_id}: {exc}")
            details_raw = raw  # Fall back to text search data

        if not details_raw:
            details_raw = raw

        # Normalize from new API camelCase into our flat dict
        details = _normalize_place(details_raw)

        place = Place(
            place_id=gp_id,
            name=details.get("name", ""),
            formatted_address=details.get("formatted_address"),
            latitude=details.get("latitude"),
            longitude=details.get("longitude"),
            rating=details.get("rating"),
            user_ratings_total=details.get("user_ratings_total"),
            formatted_phone_number=details.get("formatted_phone_number"),
            website=details.get("website"),
            opening_hours=details.get("opening_hours"),
            address_components=details.get("address_components"),
            types=details.get("types"),
            business_status=details.get("business_status"),
            price_level=details.get("price_level"),
            search_query=search_query,
            search_location=search_location,
        )

        # Upsert via INSERT … ON CONFLICT
        stmt = pg_insert(Place).values(
            place_id=place.place_id,
            name=place.name,
            formatted_address=place.formatted_address,
            latitude=place.latitude,
            longitude=place.longitude,
            rating=place.rating,
            user_ratings_total=place.user_ratings_total,
            formatted_phone_number=place.formatted_phone_number,
            website=place.website,
            opening_hours=place.opening_hours,
            address_components=place.address_components,
            types=place.types,
            business_status=place.business_status,
            price_level=place.price_level,
            search_query=place.search_query,
            search_location=place.search_location,
            created_at=datetime.datetime.utcnow(),
            updated_at=datetime.datetime.utcnow(),
        ).on_conflict_do_update(
            index_elements=["place_id"],
            set_={
                "name": place.name,
                "formatted_address": place.formatted_address,
                "latitude": place.latitude,
                "longitude": place.longitude,
                "rating": place.rating,
                "user_ratings_total": place.user_ratings_total,
                "formatted_phone_number": place.formatted_phone_number,
                "website": place.website,
                "opening_hours": place.opening_hours,
                "address_components": place.address_components,
                "types": place.types,
                "business_status": place.business_status,
                "price_level": place.price_level,
                "updated_at": datetime.datetime.utcnow(),
            },
        ).returning(Place.id)

        result = await db.execute(stmt)
        place_id_db = result.scalar_one()
        place.id = place_id_db
        places.append(place)

    await db.commit()

    # Re-fetch all places from DB to get fully hydrated objects
    if places:
        ids = [p.id for p in places]
        stmt = select(Place).where(Place.id.in_(ids))
        result = await db.execute(stmt)
        places = list(result.scalars().all())

    return places
