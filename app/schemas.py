"""Pydantic schemas for request/response validation."""

from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


# ── Request schemas ──────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500, description="Search query, e.g. 'restaurants in Dubai'")
    location: Optional[str] = Field(None, max_length=255, description="Optional location bias, e.g. '25.2048,55.2708'")
    radius_km: Optional[float] = Field(None, ge=0.1, le=50.0, description="Search radius in kilometers")
    max_pages: int = Field(3, ge=1, le=10, description="Max pagination pages per sub-region (each page ≤ 20 results)")
    enrich: bool = Field(True, description="Whether to enrich with website/email data")


class HeatmapRequest(BaseModel):
    category: str = Field(..., description="Place category/type to analyze")
    lat_min: float = Field(..., ge=-90, le=90)
    lat_max: float = Field(..., ge=-90, le=90)
    lng_min: float = Field(..., ge=-180, le=180)
    lng_max: float = Field(..., ge=-180, le=180)
    grid_size: float = Field(0.01, gt=0, le=1, description="Grid cell size in degrees")


class ScoreRequest(BaseModel):
    place_ids: list[int] = Field(..., min_length=1, description="DB IDs of places to score")


# ── Response schemas ─────────────────────────────────────────────

class EmailOut(BaseModel):
    email: str
    source: Optional[str] = None

    model_config = {"from_attributes": True}


class EnrichmentOut(BaseModel):
    homepage_status_code: Optional[int] = None
    homepage_title: Optional[str] = None
    contact_page_url: Optional[str] = None
    robots_txt_allows: Optional[bool] = None
    enrichment_error: Optional[str] = None

    model_config = {"from_attributes": True}


class PlaceOut(BaseModel):
    id: int
    place_id: str
    name: str
    formatted_address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    rating: Optional[float] = None
    user_ratings_total: Optional[int] = None
    formatted_phone_number: Optional[str] = None
    website: Optional[str] = None
    opening_hours: Optional[dict] = None
    address_components: Optional[list] = None
    types: Optional[list] = None
    business_status: Optional[str] = None
    price_level: Optional[int] = None
    classification: Optional[str] = None
    classification_confidence: Optional[float] = None
    location_score: Optional[float] = None
    competitor_density: Optional[float] = None
    emails: list[EmailOut] = []
    enrichment: Optional[EnrichmentOut] = None
    created_at: Optional[datetime] = None
    enriched_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class SearchResponse(BaseModel):
    query: str
    location: Optional[str] = None
    total_results: int
    places: list[PlaceOut]
    task_id: Optional[str] = None
    message: str = "Search completed"


class HeatmapCell(BaseModel):
    grid_lat: float
    grid_lng: float
    place_count: int
    avg_rating: Optional[float] = None
    avg_price_level: Optional[float] = None

    model_config = {"from_attributes": True}


class HeatmapResponse(BaseModel):
    category: str
    total_cells: int
    cells: list[HeatmapCell]


class LocationScoreOut(BaseModel):
    place_id: int
    place_name: str
    demand_score: float
    competition_score: float
    accessibility_score: float
    rating_score: float
    composite_score: float


class LocationScoreResponse(BaseModel):
    scores: list[LocationScoreOut]


class HealthResponse(BaseModel):
    status: str = "healthy"
    version: str = "1.0.0"
    database: str = "connected"
