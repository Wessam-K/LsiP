"""
Integration tests for FastAPI endpoints using TestClient.
Uses mocked Google API responses — no real API calls.
Migrated to Places API (New) response format.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

import os
os.environ["GOOGLE_PLACES_API_KEY"] = "test-key"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///test.db"
os.environ["DATABASE_URL_SYNC"] = "sqlite:///test.db"

from fastapi.testclient import TestClient


# We need to patch the DB before importing the app
@pytest.fixture
def client():
    """Create test client with mocked database."""
    with patch("app.db.session.init_db", new_callable=AsyncMock):
        with patch("app.db.session.engine") as mock_engine:
            mock_engine.dispose = AsyncMock()
            # Use a mock for connect
            mock_conn = AsyncMock()
            mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_conn.__aexit__ = AsyncMock(return_value=None)
            mock_conn.execute = AsyncMock()
            mock_engine.connect = MagicMock(return_value=mock_conn)

            from app.main import app
            with TestClient(app) as c:
                yield c


class TestHealthEndpoint:
    def test_health_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["version"] == "1.0.0"
        assert "database" in data

    def test_root_returns_html_or_json(self, client):
        resp = client.get("/")
        assert resp.status_code == 200


class TestSearchEndpoint:
    @patch("app.api.routes._places_client")
    @patch("app.api.routes.upsert_places")
    def test_search_empty_results(self, mock_upsert, mock_client, client):
        """Search with no results should return empty list (Places API New format)."""
        # Places API (New) returns empty list when no results
        mock_client.text_search = AsyncMock(return_value=[])
        mock_client.close = AsyncMock()

        resp = client.post("/api/v1/search", json={
            "query": "nonexistent place xyz",
            "max_pages": 1,
            "enrich": False,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_results"] == 0
        assert data["places"] == []

    def test_search_missing_query(self, client):
        """Search without query should return 422."""
        resp = client.post("/api/v1/search", json={})
        assert resp.status_code == 422


class TestPlacesEndpoints:
    @pytest.mark.skipif(
        os.environ.get("DATABASE_URL", "").startswith("sqlite"),
        reason="Requires PostgreSQL with tables"
    )
    def test_places_list_endpoint_exists(self, client):
        """Places list endpoint should be reachable."""
        # This will fail on DB but should not 404
        resp = client.get("/api/v1/places")
        # Either 200 (if mocked) or 500 (DB error), but not 404
        assert resp.status_code != 404

    @pytest.mark.skipif(
        os.environ.get("DATABASE_URL", "").startswith("sqlite"),
        reason="Requires PostgreSQL with tables"
    )
    def test_place_detail_not_found(self, client):
        """Requesting non-existent place should handle gracefully."""
        resp = client.get("/api/v1/places/99999")
        # Either 404 or 500, but endpoint exists
        assert resp.status_code in (404, 500)


class TestAPIDocumentation:
    def test_openapi_schema_accessible(self, client):
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        assert schema["info"]["title"] == "Google Places Data Ingestion & Enrichment Service"
        assert schema["info"]["version"] == "1.0.0"

    def test_docs_endpoint(self, client):
        resp = client.get("/docs")
        assert resp.status_code == 200


class TestPlacesAPINewFormat:
    """Tests to validate the Places API (New) integration."""

    def test_normalize_place_function(self):
        """Test that the _normalize_place helper correctly maps new API fields."""
        from app.services.places_client import _normalize_place

        raw = {
            "id": "ChIJN1t_tDeuEmsRUsoyG83frY4",
            "displayName": {"text": "Google HQ", "languageCode": "en"},
            "formattedAddress": "1600 Amphitheatre Pkwy, Mountain View, CA",
            "location": {"latitude": 37.4220, "longitude": -122.0841},
            "rating": 4.5,
            "userRatingCount": 12345,
            "nationalPhoneNumber": "(650) 253-0000",
            "websiteUri": "https://google.com",
            "regularOpeningHours": {
                "openNow": True,
                "weekdayDescriptions": ["Monday: 9AM–5PM"],
            },
            "types": ["point_of_interest", "establishment"],
            "businessStatus": "OPERATIONAL",
            "priceLevel": "PRICE_LEVEL_MODERATE",
        }

        result = _normalize_place(raw)

        assert result["place_id"] == "ChIJN1t_tDeuEmsRUsoyG83frY4"
        assert result["name"] == "Google HQ"
        assert result["formatted_address"] == "1600 Amphitheatre Pkwy, Mountain View, CA"
        assert result["latitude"] == 37.4220
        assert result["longitude"] == -122.0841
        assert result["rating"] == 4.5
        assert result["user_ratings_total"] == 12345
        assert result["formatted_phone_number"] == "(650) 253-0000"
        assert result["website"] == "https://google.com"
        assert result["opening_hours"]["open_now"] is True
        assert "Monday: 9AM–5PM" in result["opening_hours"]["weekday_text"]
        assert "establishment" in result["types"]
        assert result["business_status"] == "OPERATIONAL"
        assert result["price_level"] == 2  # MODERATE → 2

    def test_normalize_place_minimal(self):
        """Normalize a minimal place object (missing optional fields)."""
        from app.services.places_client import _normalize_place

        raw = {
            "id": "ChIJabc123",
            "displayName": {"text": "Tiny Shop"},
        }

        result = _normalize_place(raw)

        assert result["place_id"] == "ChIJabc123"
        assert result["name"] == "Tiny Shop"
        assert result["rating"] is None
        assert result["price_level"] is None
        assert result["opening_hours"] is None

    def test_price_level_mapping(self):
        """All price level strings should map to correct integers."""
        from app.services.places_client import _PRICE_LEVEL_MAP

        assert _PRICE_LEVEL_MAP["PRICE_LEVEL_FREE"] == 0
        assert _PRICE_LEVEL_MAP["PRICE_LEVEL_INEXPENSIVE"] == 1
        assert _PRICE_LEVEL_MAP["PRICE_LEVEL_MODERATE"] == 2
        assert _PRICE_LEVEL_MAP["PRICE_LEVEL_EXPENSIVE"] == 3
        assert _PRICE_LEVEL_MAP["PRICE_LEVEL_VERY_EXPENSIVE"] == 4
