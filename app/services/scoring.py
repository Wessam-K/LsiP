"""
Stage 5 — Location Scoring Engine: Compute composite location quality scores
based on demand signals, competition, accessibility, and ratings.
"""

from __future__ import annotations

import datetime
import math
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, update

from app.logging_config import logger
from app.db.models import Place, LocationScore


class ScoringEngine:
    """
    Computes a composite "location quality" score for each place
    based on multiple weighted dimensions.

    Dimensions:
    - Demand Score: based on user_ratings_total (proxy for foot traffic)
    - Competition Score: inverse of nearby competitor density
    - Accessibility Score: based on whether the place has a website, phone, etc.
    - Rating Score: normalized star rating

    Each sub-score is 0.0 – 1.0. The composite score is a weighted average.
    """

    # Weights for composite score
    WEIGHTS = {
        "demand": 0.30,
        "competition": 0.25,
        "accessibility": 0.20,
        "rating": 0.25,
    }

    # ── Sub-score calculations ───────────────────────────────────

    @staticmethod
    def _demand_score(place: Place) -> float:
        """
        Higher review count → higher demand signal.
        Uses log scaling so scores aren't dominated by mega-chains.
        """
        reviews = place.user_ratings_total or 0
        if reviews <= 0:
            return 0.0
        # log10(1) = 0, log10(10000) ≈ 4 → normalize to 0-1
        score = min(1.0, math.log10(reviews + 1) / 4.0)
        return round(score, 4)

    @staticmethod
    def _rating_score(place: Place) -> float:
        """Normalize 1-5 star rating to 0-1."""
        rating = place.rating
        if rating is None or rating < 1:
            return 0.0
        return round(min(1.0, (rating - 1.0) / 4.0), 4)

    @staticmethod
    def _accessibility_score(place: Place) -> float:
        """
        Score based on how accessible/findable the business is:
        - Has website
        - Has phone number
        - Has opening hours
        - Has complete address
        """
        score = 0.0
        if place.website:
            score += 0.30
        if place.formatted_phone_number:
            score += 0.25
        if place.opening_hours:
            score += 0.25
        if place.formatted_address:
            score += 0.20
        return round(score, 4)

    async def _competition_score(
        self, db: AsyncSession, place: Place, radius_km: float = 2.0
    ) -> float:
        """
        Inverse density: fewer competitors nearby → higher score.
        Measures how "uncrowded" a location is.
        """
        if place.latitude is None or place.longitude is None:
            return 0.5  # neutral if no coordinates

        lat = place.latitude
        lng = place.longitude
        lat_delta = radius_km / 111.0
        lng_delta = radius_km / (111.0 * max(0.01, math.cos(math.radians(lat))))

        result = await db.execute(
            select(func.count(Place.id)).where(
                and_(
                    Place.latitude.between(lat - lat_delta, lat + lat_delta),
                    Place.longitude.between(lng - lng_delta, lng + lng_delta),
                    Place.id != place.id,
                )
            )
        )
        nearby_count = result.scalar() or 0

        # Sigmoid-like inverse: 0 competitors → 1.0, many → approaches 0
        if nearby_count == 0:
            return 1.0
        score = 1.0 / (1.0 + math.log(nearby_count + 1))
        return round(max(0.0, min(1.0, score)), 4)

    # ── Composite score ──────────────────────────────────────────

    async def score_place(
        self, db: AsyncSession, place: Place
    ) -> LocationScore:
        """Compute and persist location score for a single place."""
        demand = self._demand_score(place)
        rating = self._rating_score(place)
        accessibility = self._accessibility_score(place)
        competition = await self._competition_score(db, place)

        composite = (
            demand * self.WEIGHTS["demand"]
            + competition * self.WEIGHTS["competition"]
            + accessibility * self.WEIGHTS["accessibility"]
            + rating * self.WEIGHTS["rating"]
        )
        composite = round(composite, 4)

        # Upsert location score
        existing = await db.execute(
            select(LocationScore).where(LocationScore.place_id == place.id)
        )
        score_obj = existing.scalar_one_or_none()

        if score_obj:
            score_obj.demand_score = demand
            score_obj.competition_score = competition
            score_obj.accessibility_score = accessibility
            score_obj.rating_score = rating
            score_obj.composite_score = composite
            score_obj.computed_at = datetime.datetime.utcnow()
        else:
            score_obj = LocationScore(
                place_id=place.id,
                demand_score=demand,
                competition_score=competition,
                accessibility_score=accessibility,
                rating_score=rating,
                composite_score=composite,
            )
            db.add(score_obj)

        # Also persist on the place itself for quick access
        await db.execute(
            update(Place)
            .where(Place.id == place.id)
            .values(location_score=composite)
        )

        logger.debug(
            f"Scored {place.name}: demand={demand} comp={competition} "
            f"access={accessibility} rating={rating} → {composite}"
        )
        return score_obj

    async def score_places(
        self, db: AsyncSession, places: list[Place]
    ) -> list[LocationScore]:
        """Score a batch of places."""
        scores = []
        for place in places:
            score = await self.score_place(db, place)
            scores.append(score)
        await db.commit()
        return scores

    async def score_all_unscored(self, db: AsyncSession) -> int:
        """Score all places that don't have a location_score."""
        result = await db.execute(
            select(Place).where(Place.location_score.is_(None))
        )
        places = list(result.scalars().all())
        if places:
            await self.score_places(db, places)
        return len(places)

    async def get_top_locations(
        self, db: AsyncSession, limit: int = 20, category: Optional[str] = None
    ) -> list[dict]:
        """Return top-scored locations, optionally filtered by search category."""
        query = (
            select(Place, LocationScore)
            .join(LocationScore, LocationScore.place_id == Place.id)
            .order_by(LocationScore.composite_score.desc())
            .limit(limit)
        )
        if category:
            query = query.where(Place.search_query.ilike(f"%{category}%"))

        result = await db.execute(query)
        rows = result.all()

        return [
            {
                "place_id": place.id,
                "name": place.name,
                "address": place.formatted_address,
                "lat": place.latitude,
                "lng": place.longitude,
                "classification": place.classification,
                "composite_score": score.composite_score,
                "demand_score": score.demand_score,
                "competition_score": score.competition_score,
                "accessibility_score": score.accessibility_score,
                "rating_score": score.rating_score,
            }
            for place, score in rows
        ]
