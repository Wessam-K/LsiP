"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-02-23
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Places table
    op.create_table(
        "places",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("place_id", sa.String(255), unique=True, nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("formatted_address", sa.Text(), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column("user_ratings_total", sa.Integer(), nullable=True),
        sa.Column("formatted_phone_number", sa.String(50), nullable=True),
        sa.Column("website", sa.Text(), nullable=True),
        sa.Column("opening_hours", sa.JSON(), nullable=True),
        sa.Column("address_components", sa.JSON(), nullable=True),
        sa.Column("types", sa.JSON(), nullable=True),
        sa.Column("business_status", sa.String(50), nullable=True),
        sa.Column("price_level", sa.Integer(), nullable=True),
        sa.Column("classification", sa.String(50), nullable=True),
        sa.Column("classification_confidence", sa.Float(), nullable=True),
        sa.Column("location_score", sa.Float(), nullable=True),
        sa.Column("competitor_density", sa.Float(), nullable=True),
        sa.Column("search_query", sa.String(500), nullable=True),
        sa.Column("search_location", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("enriched_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_places_place_id", "places", ["place_id"])
    op.create_index("ix_places_lat_lng", "places", ["latitude", "longitude"])
    op.create_index("ix_places_classification", "places", ["classification"])
    op.create_index("ix_places_search", "places", ["search_query", "search_location"])

    # Emails table
    op.create_table(
        "place_emails",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "place_id",
            sa.Integer(),
            sa.ForeignKey("places.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("source", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("place_id", "email", name="uq_place_email"),
    )

    # Enrichments table
    op.create_table(
        "place_enrichments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "place_id",
            sa.Integer(),
            sa.ForeignKey("places.id", ondelete="CASCADE"),
            unique=True,
            nullable=False,
        ),
        sa.Column("homepage_status_code", sa.Integer(), nullable=True),
        sa.Column("homepage_title", sa.Text(), nullable=True),
        sa.Column("contact_page_url", sa.Text(), nullable=True),
        sa.Column("robots_txt_allows", sa.Boolean(), nullable=True),
        sa.Column("enrichment_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )

    # Heatmap table
    op.create_table(
        "competitor_heatmap",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("grid_lat", sa.Float(), nullable=False),
        sa.Column("grid_lng", sa.Float(), nullable=False),
        sa.Column("category", sa.String(255), nullable=False),
        sa.Column("place_count", sa.Integer(), default=0),
        sa.Column("avg_rating", sa.Float(), nullable=True),
        sa.Column("avg_price_level", sa.Float(), nullable=True),
        sa.Column("computed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("grid_lat", "grid_lng", "category", name="uq_heatmap_cell"),
    )
    op.create_index("ix_heatmap_category", "competitor_heatmap", ["category"])

    # Location scores table
    op.create_table(
        "location_scores",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "place_id",
            sa.Integer(),
            sa.ForeignKey("places.id", ondelete="CASCADE"),
            unique=True,
            nullable=False,
        ),
        sa.Column("demand_score", sa.Float(), default=0.0),
        sa.Column("competition_score", sa.Float(), default=0.0),
        sa.Column("accessibility_score", sa.Float(), default=0.0),
        sa.Column("rating_score", sa.Float(), default=0.0),
        sa.Column("composite_score", sa.Float(), default=0.0),
        sa.Column("computed_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("location_scores")
    op.drop_table("competitor_heatmap")
    op.drop_table("place_enrichments")
    op.drop_table("place_emails")
    op.drop_table("places")
