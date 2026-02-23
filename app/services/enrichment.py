"""
Stage 2 — Website Enrichment: Extract emails and contact pages from place websites.
Respects robots.txt, uses async HTTP, regex-based email extraction.
"""

from __future__ import annotations

import datetime
import re
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from protego import Protego
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import get_settings
from app.logging_config import logger
from app.db.models import Place, PlaceEmail, PlaceEnrichment

settings = get_settings()

# Pre-compiled email regex — catches standard email patterns
EMAIL_REGEX = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE,
)

# Common false-positive email patterns to exclude
EXCLUDED_EMAIL_PATTERNS = {
    "example.com", "domain.com", "email.com", "test.com",
    "yoursite.com", "website.com", "sentry.io", "wixpress.com",
}

# User agent for polite crawling
USER_AGENT = "GooglePlacesEnrichmentBot/1.0 (+https://example.com/bot)"

CONTACT_LINK_PATTERNS = re.compile(
    r"(contact|kontakt|get[_\-\s]?in[_\-\s]?touch|reach[_\-\s]?us|about[_\-\s]?us)",
    re.IGNORECASE,
)


class WebsiteEnricher:
    """Extracts emails and metadata from place websites."""

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=settings.enrichment_timeout,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ── robots.txt check ─────────────────────────────────────────

    async def _check_robots(self, base_url: str) -> bool:
        """Check if our bot is allowed to crawl the URL."""
        if not settings.respect_robots_txt:
            return True
        try:
            client = await self._get_client()
            parsed = urlparse(base_url)
            robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
            resp = await client.get(robots_url)
            if resp.status_code == 200:
                rp = Protego.parse(resp.text)
                return rp.can_fetch(base_url, USER_AGENT)
        except Exception:
            pass
        return True  # If can't fetch robots.txt, assume allowed

    # ── HTML fetch ───────────────────────────────────────────────

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((httpx.ConnectError, httpx.ReadTimeout)),
        reraise=True,
    )
    async def _fetch_page(self, url: str) -> tuple[int, str]:
        client = await self._get_client()
        resp = await client.get(url)
        return resp.status_code, resp.text

    # ── Email extraction ─────────────────────────────────────────

    @staticmethod
    def _extract_emails(html: str) -> set[str]:
        """Extract email addresses from HTML, filtering false positives."""
        raw = set(EMAIL_REGEX.findall(html))
        cleaned = set()
        for email in raw:
            email_lower = email.lower()
            domain = email_lower.split("@")[1] if "@" in email_lower else ""
            # Skip image files and excluded domains
            if any(email_lower.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")):
                continue
            if any(exc in domain for exc in EXCLUDED_EMAIL_PATTERNS):
                continue
            cleaned.add(email_lower)
        return cleaned

    # ── Contact page discovery ───────────────────────────────────

    @staticmethod
    def _find_contact_page(html: str, base_url: str) -> Optional[str]:
        """Find a contact page link in the HTML."""
        soup = BeautifulSoup(html, "lxml")
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True).lower()
            href = a["href"].lower()
            if CONTACT_LINK_PATTERNS.search(text) or CONTACT_LINK_PATTERNS.search(href):
                url = urljoin(base_url, a["href"])
                # Only follow same-domain links
                if urlparse(url).netloc == urlparse(base_url).netloc:
                    return url
        return None

    @staticmethod
    def _extract_title(html: str) -> Optional[str]:
        soup = BeautifulSoup(html, "lxml")
        title = soup.find("title")
        return title.get_text(strip=True) if title else None

    # ── Main enrichment method ───────────────────────────────────

    async def enrich_place(
        self, db: AsyncSession, place: Place
    ) -> Place:
        """Enrich a single place with website data."""
        if not place.website:
            return place

        website = place.website
        logger.info(f"Enriching place={place.name} url={website}")

        enrichment_data = {
            "homepage_status_code": None,
            "homepage_title": None,
            "contact_page_url": None,
            "robots_txt_allows": None,
            "enrichment_error": None,
        }
        all_emails: dict[str, str] = {}  # email -> source

        try:
            # Check robots.txt
            allowed = await self._check_robots(website)
            enrichment_data["robots_txt_allows"] = allowed

            if not allowed:
                enrichment_data["enrichment_error"] = "Blocked by robots.txt"
                await self._save_enrichment(db, place.id, enrichment_data, all_emails)
                place.enriched_at = datetime.datetime.utcnow()
                return place

            # Fetch homepage
            status, html = await self._fetch_page(website)
            enrichment_data["homepage_status_code"] = status

            if status == 200 and html:
                enrichment_data["homepage_title"] = self._extract_title(html)

                # Extract emails from homepage
                homepage_emails = self._extract_emails(html)
                for e in homepage_emails:
                    all_emails[e] = "homepage"

                # Find and crawl contact page
                contact_url = self._find_contact_page(html, website)
                if contact_url:
                    enrichment_data["contact_page_url"] = contact_url
                    try:
                        cp_status, cp_html = await self._fetch_page(contact_url)
                        if cp_status == 200 and cp_html:
                            contact_emails = self._extract_emails(cp_html)
                            for e in contact_emails:
                                if e not in all_emails:
                                    all_emails[e] = "contact_page"
                    except Exception as exc:
                        logger.warning(f"Contact page fetch failed: {exc}")

        except Exception as exc:
            enrichment_data["enrichment_error"] = str(exc)[:500]
            logger.error(f"Enrichment error for {place.name}: {exc}")

        await self._save_enrichment(db, place.id, enrichment_data, all_emails)
        place.enriched_at = datetime.datetime.utcnow()
        await db.commit()
        return place

    # ── Persistence ──────────────────────────────────────────────

    async def _save_enrichment(
        self,
        db: AsyncSession,
        place_db_id: int,
        enrichment_data: dict,
        emails: dict[str, str],
    ):
        # Upsert enrichment record
        existing = await db.execute(
            select(PlaceEnrichment).where(PlaceEnrichment.place_id == place_db_id)
        )
        enrichment = existing.scalar_one_or_none()
        if enrichment:
            for k, v in enrichment_data.items():
                setattr(enrichment, k, v)
        else:
            enrichment = PlaceEnrichment(place_id=place_db_id, **enrichment_data)
            db.add(enrichment)

        # Add emails (dedup via unique constraint)
        for email, source in emails.items():
            existing_email = await db.execute(
                select(PlaceEmail).where(
                    PlaceEmail.place_id == place_db_id,
                    PlaceEmail.email == email,
                )
            )
            if not existing_email.scalar_one_or_none():
                db.add(PlaceEmail(place_id=place_db_id, email=email, source=source))

    async def enrich_places_batch(
        self, db: AsyncSession, places: list[Place]
    ) -> list[Place]:
        """Enrich a batch of places sequentially (polite crawling)."""
        enriched = []
        for place in places:
            try:
                p = await self.enrich_place(db, place)
                enriched.append(p)
            except Exception as exc:
                logger.error(f"Batch enrichment failed for {place.name}: {exc}")
                enriched.append(place)
        return enriched
