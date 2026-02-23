"""
Stage 3 — Classification Model: Detect brand vs local shop using
heuristic features + lightweight ML classifier.
"""

from __future__ import annotations

import re
from typing import Optional

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.logging_config import logger
from app.db.models import Place

# ── Known brand indicators ───────────────────────────────────────
KNOWN_BRANDS = {
    "mcdonald", "starbucks", "subway", "burger king", "kfc", "pizza hut",
    "domino", "dunkin", "tim hortons", "wendy", "taco bell", "chick-fil-a",
    "papa john", "popeye", "five guys", "shake shack", "chipotle",
    "walmart", "target", "costco", "ikea", "h&m", "zara", "uniqlo",
    "nike", "adidas", "apple", "samsung", "microsoft", "google",
    "amazon", "carrefour", "lulu", "spinneys", "choithrams", "al maya",
    "hardee", "baskin robbins", "cold stone", "costa coffee", "caribou",
    "nando", "applebee", "chili", "olive garden", "outback",
    "marriott", "hilton", "hyatt", "sheraton", "radisson", "ibis",
    "holiday inn", "best western", "four seasons", "ritz carlton",
    "7-eleven", "circle k", "shell", "bp", "total", "adnoc", "enoc",
}

# Typical chain/franchise patterns
CHAIN_PATTERNS = re.compile(
    r"(franchise|chain|branch|outlet|store\s*#?\d|unit\s*\d|location\s*\d)",
    re.IGNORECASE,
)

# Website domain patterns that suggest brands
BRAND_DOMAIN_INDICATORS = {
    ".com", ".co", ".global", ".international", ".inc",
}


class BusinessClassifier:
    """
    Classifies places as 'brand' (chain/franchise) or 'local' (independent).
    Uses a heuristic scoring approach that can be replaced with a trained model.
    """

    def classify(self, place: Place) -> tuple[str, float]:
        """
        Returns (classification, confidence) where classification is
        'brand' or 'local' and confidence is 0.0-1.0.
        """
        score = 0.0
        signals = 0
        max_signals = 7

        name_lower = (place.name or "").lower()

        # Signal 1: Known brand name match
        for brand in KNOWN_BRANDS:
            if brand in name_lower:
                score += 1.0
                signals += 1
                break
        else:
            signals += 1

        # Signal 2: High review count (brands tend to have more)
        if place.user_ratings_total:
            if place.user_ratings_total > 1000:
                score += 0.8
            elif place.user_ratings_total > 500:
                score += 0.5
            elif place.user_ratings_total > 100:
                score += 0.2
            signals += 1
        else:
            signals += 1

        # Signal 3: Chain pattern in name/address
        text_to_check = f"{place.name} {place.formatted_address or ''}"
        if CHAIN_PATTERNS.search(text_to_check):
            score += 0.7
        signals += 1

        # Signal 4: Website domain analysis
        if place.website:
            domain = place.website.lower()
            # Multi-location brands often have clean .com domains
            if any(ind in domain for ind in BRAND_DOMAIN_INDICATORS):
                score += 0.3
            # Local businesses more likely to have country-specific TLDs
            if any(tld in domain for tld in (".ae", ".uk", ".in", ".ph", ".pk")):
                score -= 0.2
        signals += 1

        # Signal 5: Price level consistency (brands tend to have defined pricing)
        if place.price_level is not None:
            score += 0.15  # Having price_level set slightly favors brand
        signals += 1

        # Signal 6: Business type analysis
        types = place.types or []
        brand_types = {"shopping_mall", "department_store", "supermarket", "gas_station"}
        local_types = {"cafe", "bakery", "hair_care", "laundry", "florist"}
        if any(t in brand_types for t in types):
            score += 0.4
        if any(t in local_types for t in types):
            score -= 0.3
        signals += 1

        # Signal 7: Name length / complexity (brands often shorter, standardized)
        words = name_lower.split()
        if len(words) <= 3:
            score += 0.1
        elif len(words) >= 6:
            score -= 0.1
        signals += 1

        # Normalize to 0-1 using the realistic max score
        # Max possible score: 1.0 + 0.8 + 0.7 + 0.3 + 0.15 + 0.4 + 0.1 = 3.45
        max_possible = 3.45
        confidence = max(0.0, min(1.0, score / max_possible))

        classification = "brand" if confidence >= 0.30 else "local"

        return classification, round(confidence, 3)

    async def classify_places(
        self, db: AsyncSession, places: list[Place]
    ) -> list[Place]:
        """Classify and persist classification for a batch of places."""
        for place in places:
            classification, confidence = self.classify(place)
            place.classification = classification
            place.classification_confidence = confidence

            await db.execute(
                update(Place)
                .where(Place.id == place.id)
                .values(
                    classification=classification,
                    classification_confidence=confidence,
                )
            )
            logger.debug(
                f"Classified {place.name}: {classification} ({confidence:.1%})"
            )

        await db.commit()
        return places

    async def classify_all_unclassified(self, db: AsyncSession) -> int:
        """Classify all places that haven't been classified yet."""
        result = await db.execute(
            select(Place).where(Place.classification.is_(None))
        )
        places = list(result.scalars().all())
        if places:
            await self.classify_places(db, places)
        return len(places)
