"""SQLAlchemy ORM models for the Places data pipeline."""

import datetime
from sqlalchemy import (
    Column,
    String,
    Float,
    Integer,
    Boolean,
    Text,
    DateTime,
    JSON,
    ForeignKey,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import relationship
from app.db.session import Base


class Place(Base):
    __tablename__ = "places"

    id = Column(Integer, primary_key=True, autoincrement=True)
    place_id = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(500), nullable=False)
    formatted_address = Column(Text, nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    rating = Column(Float, nullable=True)
    user_ratings_total = Column(Integer, nullable=True)
    formatted_phone_number = Column(String(50), nullable=True)
    website = Column(Text, nullable=True)
    opening_hours = Column(JSON, nullable=True)
    address_components = Column(JSON, nullable=True)
    types = Column(JSON, nullable=True)
    business_status = Column(String(50), nullable=True)
    price_level = Column(Integer, nullable=True)

    # Classification fields (Stage 3)
    classification = Column(String(50), nullable=True)  # 'brand' or 'local'
    classification_confidence = Column(Float, nullable=True)

    # Scoring (Stage 5)
    location_score = Column(Float, nullable=True)
    competitor_density = Column(Float, nullable=True)

    # Search context
    search_query = Column(String(500), nullable=True)
    search_location = Column(String(255), nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(
        DateTime,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
    )
    enriched_at = Column(DateTime, nullable=True)

    # Relationships
    emails = relationship("PlaceEmail", back_populates="place", cascade="all, delete-orphan")
    enrichment = relationship(
        "PlaceEnrichment", back_populates="place", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_places_lat_lng", "latitude", "longitude"),
        Index("ix_places_classification", "classification"),
        Index("ix_places_search", "search_query", "search_location"),
    )


class PlaceEmail(Base):
    __tablename__ = "place_emails"

    id = Column(Integer, primary_key=True, autoincrement=True)
    place_id = Column(Integer, ForeignKey("places.id", ondelete="CASCADE"), nullable=False)
    email = Column(String(320), nullable=False)
    source = Column(String(50), nullable=True)  # 'homepage', 'contact_page'
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    place = relationship("Place", back_populates="emails")

    __table_args__ = (
        UniqueConstraint("place_id", "email", name="uq_place_email"),
    )


class PlaceEnrichment(Base):
    __tablename__ = "place_enrichments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    place_id = Column(Integer, ForeignKey("places.id", ondelete="CASCADE"), unique=True, nullable=False)
    homepage_status_code = Column(Integer, nullable=True)
    homepage_title = Column(Text, nullable=True)
    contact_page_url = Column(Text, nullable=True)
    robots_txt_allows = Column(Boolean, nullable=True)
    enrichment_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    place = relationship("Place", back_populates="enrichment")


class CompetitorHeatmap(Base):
    """Pre-computed heatmap tile for competitor density (Stage 4)."""
    __tablename__ = "competitor_heatmap"

    id = Column(Integer, primary_key=True, autoincrement=True)
    grid_lat = Column(Float, nullable=False)
    grid_lng = Column(Float, nullable=False)
    category = Column(String(255), nullable=False)
    place_count = Column(Integer, default=0)
    avg_rating = Column(Float, nullable=True)
    avg_price_level = Column(Float, nullable=True)
    computed_at = Column(DateTime, default=datetime.datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("grid_lat", "grid_lng", "category", name="uq_heatmap_cell"),
        Index("ix_heatmap_category", "category"),
    )


class LocationScore(Base):
    """Composite location score for a place (Stage 5)."""
    __tablename__ = "location_scores"

    id = Column(Integer, primary_key=True, autoincrement=True)
    place_id = Column(Integer, ForeignKey("places.id", ondelete="CASCADE"), unique=True, nullable=False)
    demand_score = Column(Float, default=0.0)
    competition_score = Column(Float, default=0.0)
    accessibility_score = Column(Float, default=0.0)
    rating_score = Column(Float, default=0.0)
    composite_score = Column(Float, default=0.0)
    computed_at = Column(DateTime, default=datetime.datetime.utcnow)
