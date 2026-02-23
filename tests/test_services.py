"""
Unit tests for the classifier, scoring engine, and enrichment services.
These tests don't require a database or API key.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock

# Set test env before app imports
import os
os.environ["GOOGLE_PLACES_API_KEY"] = "test-key"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///test.db"
os.environ["DATABASE_URL_SYNC"] = "sqlite:///test.db"

from app.services.classifier import BusinessClassifier
from app.services.enrichment import WebsiteEnricher


# ── Classifier Tests ─────────────────────────────────────────────

class TestBusinessClassifier:
    def setup_method(self):
        self.classifier = BusinessClassifier()

    def _make_place(self, **kwargs):
        place = MagicMock()
        place.name = kwargs.get("name", "Test Place")
        place.user_ratings_total = kwargs.get("user_ratings_total", None)
        place.website = kwargs.get("website", None)
        place.price_level = kwargs.get("price_level", None)
        place.types = kwargs.get("types", [])
        place.formatted_address = kwargs.get("formatted_address", "123 Test St")
        return place

    def test_known_brand_detected(self):
        """Known brands should be classified as 'brand'."""
        place = self._make_place(
            name="McDonald's Dubai Marina",
            user_ratings_total=5000,
            website="https://mcdonalds.com",
        )
        classification, confidence = self.classifier.classify(place)
        assert classification == "brand"
        assert confidence > 0.4

    def test_starbucks_is_brand(self):
        place = self._make_place(
            name="Starbucks - City Walk",
            user_ratings_total=2000,
            website="https://starbucks.com",
        )
        classification, _ = self.classifier.classify(place)
        assert classification == "brand"

    def test_local_shop_detected(self):
        """Small local shop should be classified as 'local'."""
        place = self._make_place(
            name="Ahmed's Shawarma Corner",
            user_ratings_total=15,
            website=None,
            types=["cafe"],
        )
        classification, confidence = self.classifier.classify(place)
        assert classification == "local"

    def test_no_data_defaults_local(self):
        """Place with minimal data should default to local."""
        place = self._make_place(name="Unknown Place")
        classification, _ = self.classifier.classify(place)
        assert classification == "local"

    def test_high_review_count_boosts_brand_score(self):
        """High review count should push toward brand classification."""
        place = self._make_place(
            name="Generic Restaurant",
            user_ratings_total=5000,
            website="https://genericrestaurant.com",
        )
        _, confidence_high = self.classifier.classify(place)

        place_low = self._make_place(
            name="Generic Restaurant",
            user_ratings_total=5,
        )
        _, confidence_low = self.classifier.classify(place_low)

        assert confidence_high > confidence_low

    def test_chain_pattern_detection(self):
        """Franchise/chain patterns in name should be detected."""
        place = self._make_place(
            name="Pizza Place - Branch #3",
            user_ratings_total=200,
        )
        classification, confidence = self.classifier.classify(place)
        # Chain pattern should boost brand score
        assert confidence > 0.2

    def test_classify_returns_valid_range(self):
        """Confidence should always be between 0 and 1."""
        test_cases = [
            self._make_place(name="Test"),
            self._make_place(name="McDonald's", user_ratings_total=99999),
            self._make_place(name="x", types=["cafe"]),
        ]
        for place in test_cases:
            _, confidence = self.classifier.classify(place)
            assert 0.0 <= confidence <= 1.0


# ── Email Extraction Tests ───────────────────────────────────────

class TestEmailExtraction:
    def test_basic_email_extraction(self):
        html = '<p>Contact us at info@restaurant.ae or sales@shop.com</p>'
        emails = WebsiteEnricher._extract_emails(html)
        assert "info@restaurant.ae" in emails
        assert "sales@shop.com" in emails

    def test_no_emails(self):
        html = '<p>No emails here, just text.</p>'
        emails = WebsiteEnricher._extract_emails(html)
        assert len(emails) == 0

    def test_filters_image_files(self):
        """Should not extract image file references as emails."""
        html = '<img src="logo@2x.png"> <a href="mailto:real@business.com">email</a>'
        emails = WebsiteEnricher._extract_emails(html)
        assert "real@business.com" in emails
        # Should not include image-like patterns
        for email in emails:
            assert not email.endswith(".png")

    def test_filters_example_domains(self):
        """Should filter out example.com and similar test domains."""
        html = '<p>user@example.com and real@mybusiness.ae</p>'
        emails = WebsiteEnricher._extract_emails(html)
        assert "real@mybusiness.ae" in emails
        assert "user@example.com" not in emails

    def test_deduplication(self):
        """Same email appearing multiple times should be deduplicated."""
        html = '<p>info@shop.com info@shop.com INFO@SHOP.COM</p>'
        emails = WebsiteEnricher._extract_emails(html)
        assert len(emails) == 1
        assert "info@shop.com" in emails

    def test_contact_page_detection(self):
        html = '''
        <a href="/about">About</a>
        <a href="/contact-us">Contact Us</a>
        <a href="/menu">Menu</a>
        '''
        url = WebsiteEnricher._find_contact_page(html, "https://example.com")
        assert url is not None
        assert "contact" in url.lower()

    def test_no_contact_page(self):
        html = '<a href="/menu">Menu</a><a href="/gallery">Gallery</a>'
        url = WebsiteEnricher._find_contact_page(html, "https://example.com")
        assert url is None

    def test_title_extraction(self):
        html = '<html><head><title>Best Restaurant in Dubai</title></head><body></body></html>'
        title = WebsiteEnricher._extract_title(html)
        assert title == "Best Restaurant in Dubai"


# ── Scoring Engine Tests ─────────────────────────────────────────

class TestScoringEngine:
    def setup_method(self):
        from app.services.scoring import ScoringEngine
        self.engine = ScoringEngine()

    def _make_place(self, **kwargs):
        place = MagicMock()
        place.id = kwargs.get("id", 1)
        place.name = kwargs.get("name", "Test")
        place.user_ratings_total = kwargs.get("user_ratings_total", None)
        place.rating = kwargs.get("rating", None)
        place.website = kwargs.get("website", None)
        place.formatted_phone_number = kwargs.get("phone", None)
        place.opening_hours = kwargs.get("opening_hours", None)
        place.formatted_address = kwargs.get("address", None)
        place.latitude = kwargs.get("latitude", None)
        place.longitude = kwargs.get("longitude", None)
        return place

    def test_demand_score_zero_reviews(self):
        place = self._make_place(user_ratings_total=0)
        assert self.engine._demand_score(place) == 0.0

    def test_demand_score_scales_with_reviews(self):
        low = self._make_place(user_ratings_total=10)
        high = self._make_place(user_ratings_total=5000)
        assert self.engine._demand_score(high) > self.engine._demand_score(low)

    def test_demand_score_capped_at_one(self):
        place = self._make_place(user_ratings_total=999999)
        assert self.engine._demand_score(place) <= 1.0

    def test_rating_score_normalization(self):
        place_5 = self._make_place(rating=5.0)
        place_1 = self._make_place(rating=1.0)
        place_none = self._make_place(rating=None)

        assert self.engine._rating_score(place_5) == 1.0
        assert self.engine._rating_score(place_1) == 0.0
        assert self.engine._rating_score(place_none) == 0.0

    def test_accessibility_score_all_present(self):
        place = self._make_place(
            website="https://test.com",
            phone="+971501234567",
            opening_hours={"open_now": True},
            address="123 Street",
        )
        score = self.engine._accessibility_score(place)
        assert score == 1.0

    def test_accessibility_score_none(self):
        place = self._make_place()
        score = self.engine._accessibility_score(place)
        assert score == 0.0

    def test_weights_sum_to_one(self):
        total = sum(self.engine.WEIGHTS.values())
        assert abs(total - 1.0) < 0.001


# ── Schema Validation Tests ──────────────────────────────────────

class TestSchemas:
    def test_search_request_validation(self):
        from app.schemas import SearchRequest
        req = SearchRequest(query="restaurants in Dubai")
        assert req.query == "restaurants in Dubai"
        assert req.max_pages == 3
        assert req.enrich is True

    def test_search_request_min_length(self):
        from app.schemas import SearchRequest
        with pytest.raises(Exception):
            SearchRequest(query="")

    def test_heatmap_request_validation(self):
        from app.schemas import HeatmapRequest
        req = HeatmapRequest(
            category="restaurants",
            lat_min=25.0, lat_max=25.2,
            lng_min=55.0, lng_max=55.2,
        )
        assert req.grid_size == 0.01

    def test_place_out_from_attributes(self):
        from app.schemas import PlaceOut
        # Should accept from_attributes config
        assert PlaceOut.model_config.get("from_attributes") is True
