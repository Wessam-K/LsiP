"""
Stage 4 — Competitor Density Heatmap: Compute spatial density of businesses
in a grid overlay for a given category/area.
"""

from __future__ import annotations

import datetime
import math
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.logging_config import logger
from app.db.models import Place, CompetitorHeatmap


class HeatmapEngine:
    """
    Computes competitor density heatmaps by overlaying a grid
    on a geographic bounding box and counting places per cell.
    """

    async def compute_heatmap(
        self,
        db: AsyncSession,
        category: str,
        lat_min: float,
        lat_max: float,
        lng_min: float,
        lng_max: float,
        grid_size: float = 0.01,  # ~1.1 km at equator
    ) -> list[CompetitorHeatmap]:
        """
        Compute and store the heatmap for a given category in a bounding box.

        Grid cells are identified by their SW corner (grid_lat, grid_lng).
        Each cell stores the count of places and average metrics.
        """
        logger.info(
            f"Computing heatmap: category={category}, "
            f"bbox=({lat_min},{lng_min})->({lat_max},{lng_max}), grid={grid_size}"
        )

        # Build grid cells
        lat_steps = math.ceil((lat_max - lat_min) / grid_size)
        lng_steps = math.ceil((lng_max - lng_min) / grid_size)
        total_cells = lat_steps * lng_steps

        if total_cells > 10000:
            logger.warning(f"Heatmap too large ({total_cells} cells), clamping grid_size")
            grid_size = max(
                (lat_max - lat_min) / 100,
                (lng_max - lng_min) / 100,
            )
            lat_steps = math.ceil((lat_max - lat_min) / grid_size)
            lng_steps = math.ceil((lng_max - lng_min) / grid_size)

        # Delete old heatmap data for this category+bbox
        await db.execute(
            delete(CompetitorHeatmap).where(
                and_(
                    CompetitorHeatmap.category == category,
                    CompetitorHeatmap.grid_lat >= lat_min,
                    CompetitorHeatmap.grid_lat <= lat_max,
                    CompetitorHeatmap.grid_lng >= lng_min,
                    CompetitorHeatmap.grid_lng <= lng_max,
                )
            )
        )

        cells: list[CompetitorHeatmap] = []

        for i in range(lat_steps):
            cell_lat = lat_min + i * grid_size
            cell_lat_max = cell_lat + grid_size

            for j in range(lng_steps):
                cell_lng = lng_min + j * grid_size
                cell_lng_max = cell_lng + grid_size

                # Query places in this cell matching the category
                # Category match: check if any of place.types contains the category
                query = select(
                    func.count(Place.id).label("cnt"),
                    func.avg(Place.rating).label("avg_rating"),
                    func.avg(Place.price_level).label("avg_price"),
                ).where(
                    and_(
                        Place.latitude >= cell_lat,
                        Place.latitude < cell_lat_max,
                        Place.longitude >= cell_lng,
                        Place.longitude < cell_lng_max,
                        Place.latitude.isnot(None),
                        Place.longitude.isnot(None),
                    )
                )

                # If category specified, filter by search_query or types
                if category != "*":
                    query = query.where(
                        Place.search_query.ilike(f"%{category}%")
                    )

                result = await db.execute(query)
                row = result.one()

                cell_count = row.cnt or 0
                avg_rating = round(float(row.avg_rating), 2) if row.avg_rating else None
                avg_price = round(float(row.avg_price), 2) if row.avg_price else None

                cell = CompetitorHeatmap(
                    grid_lat=round(cell_lat, 6),
                    grid_lng=round(cell_lng, 6),
                    category=category,
                    place_count=cell_count,
                    avg_rating=avg_rating,
                    avg_price_level=avg_price,
                    computed_at=datetime.datetime.utcnow(),
                )
                db.add(cell)
                cells.append(cell)

        await db.commit()
        logger.info(f"Heatmap computed: {len(cells)} cells for category={category}")
        return cells

    async def get_heatmap(
        self,
        db: AsyncSession,
        category: str,
        lat_min: Optional[float] = None,
        lat_max: Optional[float] = None,
        lng_min: Optional[float] = None,
        lng_max: Optional[float] = None,
    ) -> list[CompetitorHeatmap]:
        """Retrieve stored heatmap cells."""
        query = select(CompetitorHeatmap).where(
            CompetitorHeatmap.category == category
        )
        if lat_min is not None:
            query = query.where(CompetitorHeatmap.grid_lat >= lat_min)
        if lat_max is not None:
            query = query.where(CompetitorHeatmap.grid_lat <= lat_max)
        if lng_min is not None:
            query = query.where(CompetitorHeatmap.grid_lng >= lng_min)
        if lng_max is not None:
            query = query.where(CompetitorHeatmap.grid_lng <= lng_max)

        query = query.order_by(CompetitorHeatmap.grid_lat, CompetitorHeatmap.grid_lng)
        result = await db.execute(query)
        return list(result.scalars().all())

    async def get_density_for_point(
        self,
        db: AsyncSession,
        lat: float,
        lng: float,
        radius_km: float = 2.0,
    ) -> dict:
        """Get competitor density metrics around a specific point."""
        # Approximate degrees for radius
        lat_delta = radius_km / 111.0
        lng_delta = radius_km / (111.0 * math.cos(math.radians(lat)))

        result = await db.execute(
            select(
                func.count(Place.id).label("count"),
                func.avg(Place.rating).label("avg_rating"),
                func.avg(Place.user_ratings_total).label("avg_reviews"),
            ).where(
                and_(
                    Place.latitude.between(lat - lat_delta, lat + lat_delta),
                    Place.longitude.between(lng - lng_delta, lng + lng_delta),
                )
            )
        )
        row = result.one()
        return {
            "count": row.count or 0,
            "avg_rating": round(float(row.avg_rating), 2) if row.avg_rating else None,
            "avg_reviews": round(float(row.avg_reviews), 1) if row.avg_reviews else None,
            "radius_km": radius_km,
        }
