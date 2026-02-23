"""
FastAPI router: /search endpoint with background enrichment task,
plus classification, scoring, and CSV export endpoints.
"""

from __future__ import annotations

import io
import csv
import json
import uuid
import asyncio
import datetime
from typing import Optional

from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.db.models import Place, PlaceEmail, PlaceEnrichment
from app.schemas import (
    SearchRequest,
    SearchResponse,
    PlaceOut,
    HeatmapRequest,
    HeatmapResponse,
    HeatmapCell,
    ScoreRequest,
    LocationScoreResponse,
    LocationScoreOut,
)
from app.services.places_client import GooglePlacesClient, upsert_places
from app.services.enrichment import WebsiteEnricher
from app.services.classifier import BusinessClassifier
from app.services.heatmap import HeatmapEngine
from app.services.scoring import ScoringEngine
from app.logging_config import logger

router = APIRouter()

# ── Singleton service instances ──────────────────────────────────
_places_client = GooglePlacesClient()
_enricher = WebsiteEnricher()
_classifier = BusinessClassifier()
_heatmap = HeatmapEngine()
_scoring = ScoringEngine()


# ── Background enrichment task ───────────────────────────────────

async def _background_enrich_and_score(place_ids: list[int]):
    """Run enrichment, classification, and scoring in background."""
    from app.db.session import async_session_factory

    async with async_session_factory() as db:
        try:
            stmt = (
                select(Place)
                .where(Place.id.in_(place_ids))
                .options(selectinload(Place.emails), selectinload(Place.enrichment))
            )
            result = await db.execute(stmt)
            places = list(result.scalars().all())

            # Stage 2: Enrichment
            logger.info(f"Background: enriching {len(places)} places")
            places = await _enricher.enrich_places_batch(db, places)

            # Stage 3: Classification
            logger.info(f"Background: classifying {len(places)} places")
            places = await _classifier.classify_places(db, places)

            # Stage 5: Scoring
            logger.info(f"Background: scoring {len(places)} places")
            await _scoring.score_places(db, places)

            logger.info(f"Background enrichment complete for {len(places)} places")
        except Exception as exc:
            logger.error(f"Background enrichment failed: {exc}")
        finally:
            await _enricher.close()


# ── Search endpoint ──────────────────────────────────────────────

@router.post("/search", response_model=SearchResponse)
async def search_places(
    request: SearchRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Search Google Places, store results, and optionally trigger background
    enrichment (website scraping, classification, scoring).
    """
    task_id = str(uuid.uuid4())
    logger.info(f"Search: query='{request.query}' location='{request.location}' task={task_id}")

    try:
        # Stage 1: Geo ingestion — use grid search for wider coverage
        if request.location and request.radius_km:
            try:
                parts = request.location.split(",")
                clat = float(parts[0].strip())
                clng = float(parts[1].strip())
                raw_results = await _places_client.grid_search(
                    query=request.query,
                    center_lat=clat,
                    center_lng=clng,
                    radius_km=request.radius_km,
                    max_pages=request.max_pages,
                )
            except (ValueError, IndexError):
                # fallback to simple search if location parse fails
                raw_results = await _places_client.text_search(
                    query=request.query,
                    location=request.location,
                    radius=int(request.radius_km * 1000) if request.radius_km else None,
                    max_pages=request.max_pages,
                )
        else:
            raw_results = await _places_client.text_search(
                query=request.query,
                location=request.location,
                radius=int(request.radius_km * 1000) if request.radius_km else None,
                max_pages=request.max_pages,
            )

        if not raw_results:
            return SearchResponse(
                query=request.query,
                location=request.location,
                total_results=0,
                places=[],
                task_id=task_id,
                message="No results found",
            )

        # Persist (upsert) places
        places = await upsert_places(
            db, raw_results, request.query, request.location, _places_client
        )

        # Classify immediately (fast, CPU-only)
        places = await _classifier.classify_places(db, places)

        # Trigger background enrichment if requested
        if request.enrich:
            place_ids = [p.id for p in places]
            background_tasks.add_task(_background_enrich_and_score, place_ids)

        # Re-fetch with relationships for response
        stmt = (
            select(Place)
            .where(Place.id.in_([p.id for p in places]))
            .options(selectinload(Place.emails), selectinload(Place.enrichment))
        )
        result = await db.execute(stmt)
        places = list(result.scalars().all())

        return SearchResponse(
            query=request.query,
            location=request.location,
            total_results=len(places),
            places=[PlaceOut.model_validate(p) for p in places],
            task_id=task_id,
            message="Search completed. Enrichment running in background."
            if request.enrich
            else "Search completed.",
        )

    except Exception as exc:
        logger.error(f"Search failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(exc)}")
    finally:
        await _places_client.close()


# ── SSE Search endpoint (streams progress) ──────────────────────

@router.post("/search/stream")
async def search_places_stream(
    request: SearchRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Same as /search but streams progress via Server-Sent Events.
    Emits: 'progress' events during grid search, then a final 'result' event.
    """
    task_id = str(uuid.uuid4())

    async def event_generator():
        try:
            progress_queue: asyncio.Queue = asyncio.Queue()

            async def on_progress(current, total, unique_so_far):
                await progress_queue.put({
                    "current": current,
                    "total": total,
                    "unique": unique_so_far,
                })

            # Parse location
            use_grid = False
            if request.location and request.radius_km:
                try:
                    parts = request.location.split(",")
                    clat = float(parts[0].strip())
                    clng = float(parts[1].strip())
                    use_grid = True
                except (ValueError, IndexError):
                    use_grid = False

            if use_grid:
                # Launch grid search in a task so we can drain progress
                search_task = asyncio.create_task(
                    _places_client.grid_search(
                        query=request.query,
                        center_lat=clat,
                        center_lng=clng,
                        radius_km=request.radius_km,
                        max_pages=request.max_pages,
                        on_progress=on_progress,
                    )
                )
                # Drain progress events while search runs
                while not search_task.done():
                    try:
                        prog = await asyncio.wait_for(progress_queue.get(), timeout=0.5)
                        yield f"event: progress\ndata: {json.dumps(prog)}\n\n"
                    except asyncio.TimeoutError:
                        pass
                # Drain any remaining progress events
                while not progress_queue.empty():
                    prog = await progress_queue.get()
                    yield f"event: progress\ndata: {json.dumps(prog)}\n\n"

                raw_results = search_task.result()
            else:
                raw_results = await _places_client.text_search(
                    query=request.query,
                    location=request.location,
                    radius=int(request.radius_km * 1000) if request.radius_km else None,
                    max_pages=request.max_pages,
                )

            if not raw_results:
                data = {
                    "query": request.query,
                    "location": request.location,
                    "total_results": 0,
                    "places": [],
                    "task_id": task_id,
                    "message": "No results found",
                }
                yield f"event: result\ndata: {json.dumps(data)}\n\n"
                return

            # Persist
            places = await upsert_places(
                db, raw_results, request.query, request.location, _places_client
            )

            # Classify
            places = await _classifier.classify_places(db, places)

            # Trigger background enrichment
            if request.enrich:
                place_ids = [p.id for p in places]
                asyncio.create_task(_background_enrich_and_score(place_ids))

            # Re-fetch with relationships
            stmt = (
                select(Place)
                .where(Place.id.in_([p.id for p in places]))
                .options(selectinload(Place.emails), selectinload(Place.enrichment))
            )
            result = await db.execute(stmt)
            places = list(result.scalars().all())

            data = {
                "query": request.query,
                "location": request.location,
                "total_results": len(places),
                "places": [PlaceOut.model_validate(p).model_dump(mode="json") for p in places],
                "task_id": task_id,
                "message": "Search completed. Enrichment running in background."
                if request.enrich
                else "Search completed.",
            }
            yield f"event: result\ndata: {json.dumps(data, default=str)}\n\n"

        except Exception as exc:
            logger.error(f"SSE search failed: {exc}")
            yield f"event: error\ndata: {json.dumps({'detail': str(exc)})}\n\n"
        finally:
            await _places_client.close()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Get place by ID ──────────────────────────────────────────────

@router.get("/places/{place_db_id}", response_model=PlaceOut)
async def get_place(place_db_id: int, db: AsyncSession = Depends(get_db)):
    """Retrieve a single place with its enrichment data."""
    stmt = (
        select(Place)
        .where(Place.id == place_db_id)
        .options(selectinload(Place.emails), selectinload(Place.enrichment))
    )
    result = await db.execute(stmt)
    place = result.scalar_one_or_none()
    if not place:
        raise HTTPException(status_code=404, detail="Place not found")
    return PlaceOut.model_validate(place)


# ── List places ──────────────────────────────────────────────────

@router.get("/places", response_model=list[PlaceOut])
async def list_places(
    query: Optional[str] = Query(None, description="Filter by search query"),
    classification: Optional[str] = Query(None, description="Filter: 'brand' or 'local'"),
    min_rating: Optional[float] = Query(None, ge=1, le=5),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List stored places with optional filters."""
    stmt = (
        select(Place)
        .options(selectinload(Place.emails), selectinload(Place.enrichment))
    )
    if query:
        stmt = stmt.where(Place.search_query.ilike(f"%{query}%"))
    if classification:
        stmt = stmt.where(Place.classification == classification)
    if min_rating:
        stmt = stmt.where(Place.rating >= min_rating)

    stmt = stmt.order_by(Place.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    places = result.scalars().all()
    return [PlaceOut.model_validate(p) for p in places]


# ── Heatmap endpoint ────────────────────────────────────────────

@router.post("/heatmap", response_model=HeatmapResponse)
async def compute_heatmap(
    request: HeatmapRequest,
    db: AsyncSession = Depends(get_db),
):
    """Compute competitor density heatmap for a category in a bounding box."""
    cells = await _heatmap.compute_heatmap(
        db,
        category=request.category,
        lat_min=request.lat_min,
        lat_max=request.lat_max,
        lng_min=request.lng_min,
        lng_max=request.lng_max,
        grid_size=request.grid_size,
    )
    return HeatmapResponse(
        category=request.category,
        total_cells=len(cells),
        cells=[HeatmapCell.model_validate(c) for c in cells],
    )


# ── Scoring endpoint ────────────────────────────────────────────

@router.post("/score", response_model=LocationScoreResponse)
async def score_places(
    request: ScoreRequest,
    db: AsyncSession = Depends(get_db),
):
    """Compute location scores for specified places."""
    stmt = select(Place).where(Place.id.in_(request.place_ids))
    result = await db.execute(stmt)
    places = list(result.scalars().all())

    if not places:
        raise HTTPException(status_code=404, detail="No places found with given IDs")

    scores = await _scoring.score_places(db, places)

    place_map = {p.id: p for p in places}
    return LocationScoreResponse(
        scores=[
            LocationScoreOut(
                place_id=s.place_id,
                place_name=place_map[s.place_id].name,
                demand_score=s.demand_score,
                competition_score=s.competition_score,
                accessibility_score=s.accessibility_score,
                rating_score=s.rating_score,
                composite_score=s.composite_score,
            )
            for s in scores
        ]
    )


# ── Top locations endpoint ───────────────────────────────────────

@router.get("/top-locations")
async def top_locations(
    limit: int = Query(20, ge=1, le=100),
    category: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Get top-scored locations."""
    results = await _scoring.get_top_locations(db, limit=limit, category=category)
    return {"total": len(results), "locations": results}


# ── Density around a point ───────────────────────────────────────

@router.get("/density")
async def competitor_density(
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
    radius_km: float = Query(2.0, ge=0.1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """Get competitor density metrics around a geographic point."""
    return await _heatmap.get_density_for_point(db, lat, lng, radius_km)


# ── CSV Export endpoint ──────────────────────────────────────────

@router.get("/export/csv")
async def export_csv(
    query: Optional[str] = Query(None, description="Filter by search query"),
    classification: Optional[str] = Query(None),
    min_rating: Optional[float] = Query(None, ge=1, le=5),
    limit: int = Query(500, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
):
    """Export places data as a downloadable CSV file."""
    stmt = (
        select(Place)
        .options(selectinload(Place.emails), selectinload(Place.enrichment))
    )
    if query:
        stmt = stmt.where(Place.search_query.ilike(f"%{query}%"))
    if classification:
        stmt = stmt.where(Place.classification == classification)
    if min_rating:
        stmt = stmt.where(Place.rating >= min_rating)

    stmt = stmt.order_by(Place.created_at.desc()).limit(limit)
    result = await db.execute(stmt)
    places = list(result.scalars().all())

    # Build CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Name", "Place ID", "Type", "Address",
        "Latitude", "Longitude", "Rating", "Reviews",
        "Phone", "Website", "Business Status", "Price Level",
        "Classification", "Confidence %", "Location Score %",
        "Emails", "Enriched At", "Created At",
    ])

    for p in places:
        emails_str = "; ".join(e.email for e in p.emails) if p.emails else ""
        writer.writerow([
            p.name,
            p.place_id,
            ", ".join(p.types) if p.types else "",
            p.formatted_address or "",
            p.latitude or "",
            p.longitude or "",
            p.rating or "",
            p.user_ratings_total or "",
            p.formatted_phone_number or "",
            p.website or "",
            p.business_status or "",
            p.price_level if p.price_level is not None else "",
            p.classification or "",
            f"{p.classification_confidence * 100:.0f}" if p.classification_confidence else "",
            f"{p.location_score * 100:.0f}" if p.location_score else "",
            emails_str,
            p.enriched_at.isoformat() if p.enriched_at else "",
            p.created_at.isoformat() if p.created_at else "",
        ])

    # Encode with UTF-8 BOM so Excel opens Arabic/Unicode correctly
    csv_bytes = b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")
    output.seek(0)
    filename = f"places_export_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([csv_bytes]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
